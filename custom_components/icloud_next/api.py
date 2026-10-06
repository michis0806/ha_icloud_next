"""Blockierende Anbindung an pyicloud — nur im Executor aufrufen.

Zwei Dinge macht diese Schicht anders als die offizielle icloud-Integration:

1. pyicloud fordert nach der Passwortprüfung sofort selbst einen 2FA-Code an
   (2.6.x: Push und SMS gleichzeitig, 2.7.x: Push, sonst SMS). Apple schickt bei
   API-Anmeldungen von sich aus nichts. Wir unterdrücken diesen automatischen
   Versand, damit der Nutzer im Config-Flow zuerst Push oder SMS wählen kann —
   und damit ein Poll mit abgelaufener Anmeldung nicht unaufgefordert Codes
   verschickt.
2. Seit pyicloud 2.3 liest ein normaler Abruf nur Apples Positions-Cache.
   ``ortung()`` stößt deshalb aktiv eine Ortung an; das Ergebnis holt der
   Coordinator nach ``LOCATE_WAIT`` Sekunden mit einem zweiten Abruf ab.

Die 2FA-Steuerung nutzt interne pyicloud-Methoden. Sie sind in 2.6.5 und 2.7.0
gleich benannt; fehlt eine davon in einer späteren Version, meldet
``zustellwege()`` keine Wege statt mit einem AttributeError abzubrechen.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudException,
    PyiCloudFailedLoginException,
    PyiCloudNoDevicesException,
)

from .const import METHOD_PUSH, METHOD_SMS

_LOGGER = logging.getLogger(__name__)

# Der Hintergrund-Thread von pyicloud würde sonst alle 5 Min ohne Ortung den
# Cache lesen und parallel zu unseren Abrufen die Gerätedaten überschreiben.
_MONITOR_INTERVAL = 24 * 3600


class ICloudError(Exception):
    """Apple nicht erreichbar oder unerwartete Antwort."""


class ICloudAuthError(ICloudError):
    """Passwort falsch oder Anmeldung abgelaufen (2FA nötig)."""


class ICloudSecurityKeyError(ICloudAuthError):
    """Das Konto verlangt einen Sicherheitsschlüssel — wird nicht unterstützt."""


class _Dienst(PyiCloudService):
    """PyiCloudService ohne automatischen 2FA-Versand."""

    _versand_erlaubt = False

    def request_2fa_code(self) -> bool:  # pyicloud 2.7
        if not self._versand_erlaubt:
            _LOGGER.debug("Automatischer 2FA-Versand unterdrückt")
            return False
        return super().request_2fa_code()

    def _request_2fa_code(self) -> None:  # pyicloud 2.6
        _LOGGER.debug("Automatischer 2FA-Versand unterdrückt")


def sitzungsordner(config_dir: str, apple_id: str) -> str:
    """Eigener Ordner je Konto, getrennt von der Session der icloud-Integration."""
    name = re.sub(r"[^a-z0-9]+", "_", apple_id.lower()).strip("_")
    pfad = Path(config_dir) / ".storage" / "icloud_next" / name
    pfad.mkdir(parents=True, exist_ok=True)
    return str(pfad)


def anmelden(apple_id: str, passwort: str, ordner: str, familie: bool) -> _Dienst:
    """Mit gespeicherter Session oder Passwort anmelden; verschickt nie einen Code."""
    try:
        return _Dienst(
            apple_id,
            passwort,
            cookie_directory=ordner,
            with_family=familie,
            refresh_interval=_MONITOR_INTERVAL,
        )
    except PyiCloudFailedLoginException as err:
        # Auch eine abgelaufene Session landet hier, wenn der Passwort-Fallback
        # scheitert — beides heißt für den Nutzer: neu anmelden.
        raise ICloudAuthError(str(err)) from err
    except (PyiCloudException, OSError) as err:
        raise ICloudError(str(err)) from err


def braucht_2fa(api: _Dienst) -> bool:
    if api.requires_2fa and api.security_key_names:
        raise ICloudSecurityKeyError(", ".join(api.security_key_names))
    return api.requires_2fa


def zustellwege(api: _Dienst) -> list[str]:
    """Welche Wege Apple für den 2FA-Code dieses Kontos anbietet."""
    wege: list[str] = []
    try:
        # Vertrauenswürdige Geräte gibt es praktisch immer; der Push läuft über
        # die neue "Bridge" oder den alten trusteddevice-Endpunkt.
        wege.append(METHOD_PUSH)
        if api._trusted_phone_number() is not None:
            wege.append(METHOD_SMS)
    except AttributeError:
        _LOGGER.warning("Diese pyicloud-Version bietet keine Wahl des 2FA-Wegs")
        return []
    return wege


def telefonnummer(api: _Dienst) -> str | None:
    """Apples maskierte Anzeige der SMS-Nummer, z. B. '•••• ••36'."""
    nummer = api._auth_data.get("trustedPhoneNumber") or {}
    if not nummer:
        nummern = (api._auth_data.get("phoneNumberVerification") or {}).get(
            "trustedPhoneNumbers"
        ) or []
        nummer = nummern[0] if nummern else {}
    return nummer.get("numberWithDialCode") or nummer.get("obfuscatedNumber")


def code_senden(api: _Dienst, weg: str) -> None:
    """Den 2FA-Code über den gewählten Weg anfordern."""
    try:
        if weg == METHOD_SMS:
            api._request_sms_2fa_code()
            return
        if api._supports_trusted_device_bridge():
            api._versand_erlaubt = True
            try:
                if not api.request_2fa_code():
                    raise ICloudError("Apple hat den Push nicht angenommen")
            finally:
                api._versand_erlaubt = False
            return
        # Älterer Weg ohne Bridge: Apple zeigt den Code auf den Geräten an.
        api.session.get(
            f"{api._auth_endpoint}/verify/trusteddevice",
            headers=api._get_auth_headers({"Accept": "application/json"}),
        )
        api._set_two_factor_delivery_state("trusted_device")
    except PyiCloudException as err:
        raise ICloudError(str(err)) from err


def code_pruefen(api: _Dienst, code: str) -> bool:
    """Code bestätigen und die Session als vertrauenswürdig markieren."""
    try:
        if not api.validate_2fa_code(code.strip()):
            return False
    except PyiCloudException as err:
        _LOGGER.debug("2FA-Prüfung fehlgeschlagen: %s", err)
        return False
    if not api.is_trusted_session:
        api.trust_session()
    return api.is_trusted_session


# ---------------------------------------------------------------- Datenabruf


def _ms(wert: Any) -> datetime | None:
    if not wert:
        return None
    return datetime.fromtimestamp(wert / 1000, UTC)


def _person_id(prs_id: str | None) -> str | None:
    """prsId ist die DSID als Base64 mit '~' statt '='."""
    if not prs_id:
        return None
    try:
        return base64.b64decode(prs_id.replace("~", "=")).decode()
    except (ValueError, UnicodeDecodeError):
        return prs_id


def ortung(api: _Dienst, aktiv: bool) -> dict[str, Any]:
    """Find-My-Daten lesen; bei ``aktiv`` vorher eine Ortung bei Apple anstoßen."""
    try:
        api.devices.refresh(locate=aktiv)
        geraete = {d.data["id"]: dict(d.data) for d in api.devices}
        info = dict(api.devices.user_info or {})
    except PyiCloudNoDevicesException:
        geraete, info = {}, {}
    except PyiCloudAuthRequiredException as err:
        raise ICloudAuthError(str(err)) from err
    except (PyiCloudException, OSError) as err:
        raise ICloudError(str(err)) from err
    if api.requires_2fa:
        raise ICloudAuthError("Apple verlangt eine neue Anmeldung mit 2FA")

    personen = {
        _person_id(k): f"{v.get('firstName', '')} {v.get('lastName', '')}".strip()
        for k, v in (info.get("membersInfo") or {}).items()
    }
    inhaber = f"{info.get('firstName', '')} {info.get('lastName', '')}".strip()

    ergebnis: dict[str, dict[str, Any]] = {}
    for geraet_id, d in geraete.items():
        ort = d.get("location") or {}
        person = _person_id(d.get("prsId"))
        akku = d.get("batteryLevel")
        ergebnis[geraet_id] = {
            "id": geraet_id,
            "name": d.get("name") or d.get("deviceDisplayName") or geraet_id,
            "modell": d.get("deviceDisplayName") or d.get("modelDisplayName"),
            "modell_kennung": d.get("rawDeviceModel"),
            "klasse": d.get("deviceClass"),
            # Apple-Feature-Flag "MSG": Gerät kann eine Mitteilung anzeigen
            "nachricht_moeglich": bool((d.get("features") or {}).get("MSG")),
            "zubehoer": d.get("deviceClass") == "Accessory"
            or bool(d.get("isConsideredAccessory")),
            "person": person,  # None = Kontoinhaber
            "person_name": personen.get(person, person) if person else inhaber,
            "online": str(d.get("deviceStatus")) == "200",
            "status_code": str(d.get("deviceStatus")),
            "akku": round(akku * 100) if akku is not None else None,
            "ladezustand": d.get("batteryStatus"),
            "stromsparmodus": d.get("lowPowerMode"),
            "ortet": d.get("isLocating"),
            "breite": ort.get("latitude"),
            "laenge": ort.get("longitude"),
            "genauigkeit": ort.get("horizontalAccuracy"),
            "hoehe": ort.get("altitude"),
            "ortungszeit": _ms(ort.get("timeStamp")),
            "veraltet": ort.get("isOld"),
            "ungenau": ort.get("isInaccurate"),
            "positionsquelle": ort.get("positionType"),
        }
    # Apple lädt die Familiengeräte asynchron nach; steht ein Mitglied noch auf
    # LOADING, fehlen seine Geräte in dieser Antwort. Dann darf nichts aufgeräumt werden.
    vollstaendig = all(
        v.get("deviceFetchStatus") == "DONE" for v in (info.get("membersInfo") or {}).values()
    )
    return {
        "geraete": ergebnis,
        "personen": personen,
        "inhaber": inhaber,
        "familie_vollstaendig": vollstaendig,
    }


def nachricht_senden(
    api: _Dienst, geraet_id: str, titel: str, text: str, ton: bool
) -> None:
    """Eine Mitteilung auf einem Gerät anzeigen ("Wo ist?" → Mitteilung anzeigen)."""
    try:
        geraet = next((d for d in api.devices if d.data.get("id") == geraet_id), None)
        if geraet is None:
            raise ICloudError("Gerät wird von Apple nicht mehr gemeldet")
        geraet.display_message(subject=titel, message=text, sounds=ton)
    except PyiCloudAuthRequiredException as err:
        raise ICloudAuthError(str(err)) from err
    except (PyiCloudException, OSError) as err:
        raise ICloudError(str(err)) from err


def konto(api: _Dienst, familie: bool) -> dict[str, Any]:
    """Speicher, Abo und die am eigenen Konto angemeldeten Geräte."""
    acc = api.account
    try:
        speicher = acc.session.post(acc._acc_storage_url, params=acc.params).json()
        geraete = acc.session.get(acc._acc_devices_url, params=acc.params).json()
        try:
            plan = acc.summary_plan
        except PyiCloudAPIResponseException:
            plan = {}
    except PyiCloudAuthRequiredException as err:
        raise ICloudAuthError(str(err)) from err
    except (PyiCloudException, OSError, ValueError) as err:
        raise ICloudError(str(err)) from err

    info = speicher.get("storageUsageInfo") or {}
    quota = speicher.get("quotaStatus") or {}
    fam = speicher.get("familyStorageUsageInfo") or {}
    limit = (plan.get("summary") or {}) if isinstance(plan, dict) else {}
    return {
        "speicher": {
            "belegt": info.get("usedStorageInBytes"),
            "gesamt": info.get("totalStorageInBytes"),
            "fast_voll": quota.get("almost-full"),
            "ueber_kontingent": quota.get("overQuota"),
            "bezahlt": quota.get("paidQuota"),
            "plan": f"{limit.get('limit')} {limit.get('limitUnits')}"
            if limit.get("limit")
            else None,
            "bereiche": {
                b["mediaKey"]: {"name": b.get("displayLabel"), "bytes": b.get("usageInBytes")}
                for b in speicher.get("storageUsageByMedia") or []
                if b.get("mediaKey")
            },
            "familie_gesamt": fam.get("usageInBytes") if familie else None,
            "familie": {
                str(m.get("dsid") or m.get("id")): {
                    "name": m.get("fullName") or m.get("firstName"),
                    "bytes": m.get("usageInBytes"),
                }
                for m in (fam.get("familyMembers") or [])
            }
            if familie
            else {},
        },
        # Seriennummer und OS-Version gibt Apple nur für Geräte des eigenen Kontos
        # heraus; IMEI, UDID und Zahlungsmittel werden bewusst nicht übernommen.
        "kontogeraete": [
            {
                "name": g.get("name"),
                "modell": g.get("modelDisplayName"),
                "modell_kennung": g.get("model"),
                "seriennummer": g.get("serialNumber"),
                "betriebssystem": (g.get("osVersion") or "").replace(";", " ").strip(),
            }
            for g in geraete.get("devices") or []
        ],
    }
