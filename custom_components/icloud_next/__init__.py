"""iCloud Next — Find My mit aktiver Ortung, iCloud-Speicher und Kontogeräte.

Gegenüber der offiziellen icloud-Integration:
- jede Abfrage stößt bei Apple eine aktive Ortung an und liest das Ergebnis
  ``LOCATE_WAIT`` Sekunden später (sonst kommt nur Apples Positions-Cache),
- Ortungszeit, "veraltet" und Positionsquelle werden mitgeliefert,
- eine abgelaufene Anmeldung führt in den Reauth-Flow statt in setup_error,
  und es wird nie unaufgefordert ein 2FA-Code verschickt,
- dazu iCloud-Speicher (auch je Familienmitglied) sowie Seriennummer und
  OS-Version der Geräte des eigenen Kontos.

pyicloud arbeitet blockierend mit requests; alle Aufrufe laufen im Executor.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import api
from .const import (
    ACCOUNT_INTERVAL,
    CONF_APPLE_ID,
    CONF_SCAN_INTERVAL,
    CONF_WITH_FAMILY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FRESH_FACTOR,
    GRACE_SECONDS,
    LOCATE_WAIT,
    PERSON_DEVICE_PRIORITY,
)
from .entity import aufraeumen, darf_geloescht_werden, geraete_registrieren

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["binary_sensor", "button", "device_tracker", "notify", "sensor"]


def familie_aktiv(entry: ConfigEntry) -> bool:
    return entry.options.get(CONF_WITH_FAMILY, entry.data.get(CONF_WITH_FAMILY, True))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    apple_id = entry.data[CONF_APPLE_ID]
    familie = familie_aktiv(entry)
    ordner = await hass.async_add_executor_job(
        api.sitzungsordner, hass.config.config_dir, apple_id
    )
    try:
        dienst = await hass.async_add_executor_job(
            api.anmelden, apple_id, entry.data[CONF_PASSWORD], ordner, familie
        )
        if await hass.async_add_executor_job(api.braucht_2fa, dienst):
            raise ConfigEntryAuthFailed("Apple verlangt eine neue Anmeldung mit 2FA")
    except api.ICloudAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except api.ICloudError as err:
        raise ConfigEntryNotReady(str(err)) from err

    ortung = OrtungCoordinator(hass, entry, dienst)
    konto = KontoCoordinator(hass, entry, dienst, familie)
    # Konto zuerst: Seriennummer und OS-Version landen beim Anlegen der Geräte
    # gleich im Device-Registry.
    await konto.async_config_entry_first_refresh()
    await ortung.async_config_entry_first_refresh()

    entry.runtime_data = {
        "ortung": ortung,
        "konto": konto,
        "dienst": dienst,
        "unique_ids": set(),
        "plattformen": set(),
    }
    geraete_registrieren(hass, entry)
    # OS-Updates und neue Kontogeräte ins Device-Registry übernehmen
    entry.async_on_unload(konto.async_add_listener(lambda: geraete_registrieren(hass, entry)))
    entry.async_on_unload(entry.add_update_listener(_optionen_geaendert))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Nur aufräumen, wenn alle Plattformen vollständig geladen haben — sonst würden
    # die Entities einer gescheiterten Plattform fälschlich entfernt.
    if entry.runtime_data["plattformen"] == set(PLATFORMS):
        aufraeumen(hass, entry, entry.runtime_data["unique_ids"])
    # Gleich nach dem Start einmal aktiv orten statt erst nach dem ersten Intervall.
    entry.async_create_background_task(
        hass, ortung.async_refresh(), f"{DOMAIN}_erste_ortung"
    )
    return True


async def _optionen_geaendert(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        dienst = entry.runtime_data["dienst"]
        # Den Monitor-Thread von pyicloud beenden, sonst läuft er nach dem Reload weiter.
        stop = getattr(getattr(dienst, "_devices", None), "stop_event", None)
        if stop is not None:
            stop.set()
    return ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, geraet: dr.DeviceEntry
) -> bool:
    """Löschen-Knopf in der Geräteansicht — nur für Geräte, die Apple nicht mehr meldet."""
    return darf_geloescht_werden(entry, geraet)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Beim Löschen auch die gespeicherte Apple-Session entfernen."""
    ordner = await hass.async_add_executor_job(
        api.sitzungsordner, hass.config.config_dir, entry.data[CONF_APPLE_ID]
    )
    await hass.async_add_executor_job(shutil.rmtree, Path(ordner), True)


class _Basis(DataUpdateCoordinator[dict[str, Any]]):
    """Gemeinsame Fehlerbehandlung: Reauth bei Anmeldefehlern, Karenz bei Störungen."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, dienst: Any, name: str, minuten: int
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{name}",
            update_interval=timedelta(minutes=minuten),
        )
        self.dienst = dienst
        self._last_ok = 0.0

    async def _abrufen(self) -> dict[str, Any]:
        raise NotImplementedError

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            daten = await self._abrufen()
        except api.ICloudAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except api.ICloudError as err:
            if self.data is not None and time.monotonic() - self._last_ok < GRACE_SECONDS:
                _LOGGER.warning("iCloud-Abruf fehlgeschlagen, nutze letzte Werte: %s", err)
                return self.data
            raise UpdateFailed(str(err)) from err
        self._last_ok = time.monotonic()
        return daten


class OrtungCoordinator(_Basis):
    """Aktive Ortung aller Geräte und Zusammenfassung je Person."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, dienst: Any) -> None:
        self.intervall = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(hass, entry, dienst, "ortung", self.intervall)
        # Der erste Abruf beim Start liest nur den Cache, damit die Einrichtung
        # nicht LOCATE_WAIT Sekunden blockiert; ab dem zweiten wird aktiv geortet.
        self._aktiv = False

    async def _abrufen(self) -> dict[str, Any]:
        if self._aktiv:
            await self.hass.async_add_executor_job(api.ortung, self.dienst, True)
            await asyncio.sleep(LOCATE_WAIT)
        self._aktiv = True
        daten = await self.hass.async_add_executor_job(api.ortung, self.dienst, False)
        daten["zusammengefasst"] = self._personen(daten)
        return daten

    def _personen(self, daten: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Je Person die beste Position — Apple selbst liefert nur Geräte.

        Bevorzugt wird das erste frische Gerät in PERSON_DEVICE_PRIORITY (iPhone vor
        Watch vor iPad), damit eine zu Hause ladende Uhr nicht die Position des
        unterwegs mitgeführten iPhones überschreibt. Ist keines frisch, gewinnt die
        jüngste Ortung irgendeines Nicht-Zubehör-Geräts.
        """
        grenze = datetime.now(UTC) - timedelta(minutes=self.intervall * FRESH_FACTOR)
        je_person: dict[str, list[dict[str, Any]]] = {}
        for g in daten["geraete"].values():
            if g["zubehoer"] or g["breite"] is None or g["ortungszeit"] is None:
                continue
            je_person.setdefault(g["person"] or "", []).append(g)

        ergebnis: dict[str, dict[str, Any]] = {}
        for person, geraete in je_person.items():
            frisch = [g for g in geraete if not g["veraltet"] and g["ortungszeit"] >= grenze]
            wahl = None
            for klasse in PERSON_DEVICE_PRIORITY:
                kandidaten = [g for g in frisch if g["klasse"] == klasse]
                if kandidaten:
                    wahl = max(kandidaten, key=lambda g: g["ortungszeit"])
                    break
            if wahl is None:
                wahl = max(geraete, key=lambda g: g["ortungszeit"])
            ergebnis[person] = {
                "name": wahl["person_name"],
                "geraet": wahl["name"],
                "frisch": wahl in frisch,
                **{k: wahl[k] for k in (
                    "breite", "laenge", "genauigkeit", "ortungszeit",
                    "veraltet", "positionsquelle",
                )},
            }
        return ergebnis


class KontoCoordinator(_Basis):
    """Speicher und Kontogeräte — ändern sich selten, daher stündlich."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, dienst: Any, familie: bool
    ) -> None:
        super().__init__(hass, entry, dienst, "konto", ACCOUNT_INTERVAL)
        self._familie = familie

    async def _abrufen(self) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(api.konto, self.dienst, self._familie)
