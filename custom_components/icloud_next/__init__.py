"""iCloud Next — Find My mit aktiver Ortung, iCloud-Speicher und Kontogeräte.

Gegenüber der offiziellen icloud-Integration:
- jede Abfrage stößt bei Apple eine aktive Ortung an und liest das Ergebnis
  ``LOCATE_WAIT`` Sekunden später (sonst kommt nur Apples Positions-Cache),
- Ortungszeit, "veraltet" und Positionsquelle werden mitgeliefert,
- eine abgelaufene Anmeldung wird erst still erneuert, dann mehrfach zeitversetzt
  frisch angemeldet und führt erst danach in den Reauth-Flow statt in setup_error;
  es wird nie unaufgefordert ein 2FA-Code verschickt,
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
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
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
    RELOGIN_DELAYS,
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

    sitzung = Sitzung(hass, entry, dienst, ordner, familie)
    ortung = OrtungCoordinator(hass, entry, sitzung)
    konto = KontoCoordinator(hass, entry, sitzung, familie)
    sitzung.coordinators = [ortung, konto]
    entry.async_on_unload(sitzung.abbrechen)
    # Konto zuerst: Seriennummer und OS-Version landen beim Anlegen der Geräte
    # gleich im Device-Registry.
    await konto.async_config_entry_first_refresh()
    await ortung.async_config_entry_first_refresh()

    entry.runtime_data = {
        "ortung": ortung,
        "konto": konto,
        "sitzung": sitzung,
        "unique_ids": set(),
        "plattformen": set(),
    }
    geraete_registrieren(hass, entry)
    # OS-Updates und neue Kontogeräte ins Device-Registry übernehmen
    entry.async_on_unload(konto.async_add_listener(lambda: geraete_registrieren(hass, entry)))
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


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        # Den Monitor-Thread von pyicloud beenden, sonst läuft er nach dem Reload weiter.
        api.beenden(entry.runtime_data["sitzung"].dienst)
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


class Sitzung:
    """pyicloud-Dienst beider Coordinators und die stille Wiederanmeldung.

    Lehnt Apple die Sitzung ab und verlangt auch das stille Erneuern 2FA, ist das
    oft nur vorübergehend. Statt sofort den Reauth-Flow zu starten, wird nach den
    Pausen in RELOGIN_DELAYS frisch angemeldet — genau das, was der Reauth-Dialog
    mit gespeichertem Passwort tut. Erst wenn auch der letzte Versuch scheitert,
    muss der Nutzer ran. Vor jedem Versuch wird der letzte gute Stand der Session
    zurückgelegt, damit ein gescheiterter Versuch den trust_token nicht vernichtet.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, dienst: Any, ordner: str, familie: bool
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.dienst = dienst
        self.coordinators: list[_Basis] = []
        self._ordner = ordner
        self._familie = familie
        self._stand: dict[str, bytes] | None = None
        self._versuch = 0
        self._timer: CALLBACK_TYPE | None = None
        self.abgelehnt = False
        self.lock = asyncio.Lock()

    async def sichern(self) -> None:
        """Stand vor einer Anmeldung merken, die die Session überschreiben kann."""
        self._stand = await self.hass.async_add_executor_job(
            api.sitzung_sichern, self._ordner
        )

    def verwerfen(self) -> None:
        """Abruf geklappt — die Session auf der Platte ist wieder der gute Stand."""
        self._stand = None

    def ablehnen(self, err: Exception) -> None:
        """Apple verlangt eine neue Anmeldung — Wiederholungsversuche einplanen."""
        if self.abgelehnt:
            return
        self.abgelehnt = True
        self._versuch = 0
        _LOGGER.warning(
            "Apple lehnt die Anmeldung ab (%s); melde mich nach %s Minuten ohne Code "
            "neu an, erst danach ist eine Neuanmeldung durch den Nutzer nötig",
            err,
            "/".join(str(m) for m in RELOGIN_DELAYS),
        )
        self._planen()

    def _planen(self) -> None:
        self._timer = async_call_later(
            self.hass, RELOGIN_DELAYS[self._versuch] * 60, self._versuchen
        )

    def abbrechen(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    async def _versuchen(self, _jetzt: Any) -> None:
        self._timer = None
        self._versuch += 1
        async with self.lock:
            try:
                if self._stand is not None:
                    await self.hass.async_add_executor_job(
                        api.sitzung_zuruecklegen, self._ordner, self._stand
                    )
                neu = await self.hass.async_add_executor_job(
                    api.frisch_anmelden,
                    self.entry.data[CONF_APPLE_ID],
                    self.entry.data[CONF_PASSWORD],
                    self._ordner,
                    self._familie,
                )
            except Exception as err:  # noqa: BLE001 — jeder Fehlschlag zählt als Versuch
                letzter = self._versuch >= len(RELOGIN_DELAYS)
                if letzter or isinstance(err, api.ICloudSecurityKeyError):
                    _LOGGER.warning(
                        "Anmeldung auch im %d. Versuch abgelehnt (%s); Neuanmeldung nötig",
                        self._versuch,
                        err,
                    )
                    if self._stand is not None:
                        await self.hass.async_add_executor_job(
                            api.sitzung_zuruecklegen, self._ordner, self._stand
                        )
                    self.entry.async_start_reauth(self.hass)
                    return
                _LOGGER.info(
                    "Anmeldeversuch %d abgelehnt (%s), nächster in %d Minuten",
                    self._versuch,
                    err,
                    RELOGIN_DELAYS[self._versuch],
                )
                self._planen()
                return
            await self.hass.async_add_executor_job(api.beenden, self.dienst)
            self.dienst = neu
            self.abgelehnt = False
            self._stand = None
        _LOGGER.warning("Anmeldung im %d. Versuch ohne Code wiederhergestellt", self._versuch)
        for coordinator in self.coordinators:
            coordinator.zuruecksetzen()
            await coordinator.async_refresh()


class _Basis(DataUpdateCoordinator[dict[str, Any]]):
    """Gemeinsame Fehlerbehandlung: Reauth bei Anmeldefehlern, Karenz bei Störungen."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, sitzung: Sitzung, name: str, minuten: int
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{name}",
            update_interval=timedelta(minutes=minuten),
        )
        self.sitzung = sitzung
        self._last_ok = 0.0
        self._sitzungsfehler = 0

    @property
    def dienst(self) -> Any:
        """Immer der aktuelle Dienst — eine stille Wiederanmeldung ersetzt ihn."""
        return self.sitzung.dienst

    def zuruecksetzen(self) -> None:
        self._sitzungsfehler = 0

    async def _abrufen(self) -> dict[str, Any]:
        raise NotImplementedError

    async def _async_update_data(self) -> dict[str, Any]:
        if self.sitzung.abgelehnt:
            # Die Wiederanmeldung läuft zeitversetzt; bis dahin nicht bei Apple anklopfen.
            return self._karenz(api.ICloudError("warte auf erneute Anmeldung"))
        try:
            try:
                daten = await self._abrufen()
            except api.ICloudSessionError as err:
                # Ein einzelnes 409/421 ist meist beim nächsten Abruf vorbei. Kommt es
                # zweimal hintereinander, die Sitzung erneuern und gleich nochmal abrufen.
                self._sitzungsfehler += 1
                if self._sitzungsfehler < 2:
                    raise
                _LOGGER.info("Apple lehnt die Sitzung erneut ab (%s), melde neu an", err)
                async with self.sitzung.lock:
                    await self.sitzung.sichern()
                    await self.hass.async_add_executor_job(api.neu_anmelden, self.dienst)
                daten = await self._abrufen()
        except api.ICloudSecurityKeyError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except api.ICloudAuthError as err:
            return self._abgelehnt(err)
        except api.ICloudSessionError as err:
            if self._sitzungsfehler >= 2:
                # Trotz stiller Erneuerung abgelehnt
                return self._abgelehnt(err)
            return self._karenz(err)
        except api.ICloudError as err:
            return self._karenz(err)
        self._last_ok = time.monotonic()
        self._sitzungsfehler = 0
        self.sitzung.verwerfen()
        return daten

    def _abgelehnt(self, err: api.ICloudError) -> dict[str, Any]:
        self.sitzung.ablehnen(err)
        return self._karenz(err)

    def _karenz(self, err: api.ICloudError) -> dict[str, Any]:
        """Bei Störungen bis GRACE_SECONDS die letzten Werte behalten."""
        if self.data is not None and time.monotonic() - self._last_ok < GRACE_SECONDS:
            _LOGGER.warning("iCloud-Abruf fehlgeschlagen, nutze letzte Werte: %s", err)
            return self.data
        raise UpdateFailed(str(err)) from err


class OrtungCoordinator(_Basis):
    """Aktive Ortung aller Geräte und Zusammenfassung je Person."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, sitzung: Sitzung) -> None:
        self.intervall = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(hass, entry, sitzung, "ortung", self.intervall)
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
        self, hass: HomeAssistant, entry: ConfigEntry, sitzung: Sitzung, familie: bool
    ) -> None:
        super().__init__(hass, entry, sitzung, "konto", ACCOUNT_INTERVAL)
        self._familie = familie

    async def _abrufen(self) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(api.konto, self.dienst, self._familie)
