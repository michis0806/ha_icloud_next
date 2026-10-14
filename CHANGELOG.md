# Changelog

## 0.3.4

- Supports pyicloud 2026.10.0 (minimum is now 2.7.0, which Home Assistant 2026.10
  ships). Among other things, 2026.10.0 falls back to a password sign-in when a
  stored session token has expired, reports network failures as such instead of as
  a failed login, keeps the Find My cookie between sign-ins and accepts verification
  codes that Apple confirms with an HTTP 409.
- With pyicloud 2026.10.0 its background refresh thread is switched off completely
  instead of running once a day.
- Removed the workaround for pyicloud 2.6.

## 0.3.3

- Fewer unnecessary 2FA prompts: if Apple asks for a verification code while the
  session is being renewed silently, re-authentication no longer starts right away.
  The integration signs in again without a code after 15, 30 and 60 minutes and only
  asks you if that fails too. Apple often asks for the code only temporarily –
  rejected at night, the same sign-in went through without a code in the morning.
- Before each of these attempts the last good session is restored, so a failed
  attempt cannot lose the trust token.
- Options now reload the integration via `OptionsFlowWithReload` instead of an update
  listener (fixes the "has an update listener" warning, required from Home Assistant
  2026.12).

## 0.3.2

- If Apple rejects the session twice in a row (HTTP 409/421), the integration signs
  in again silently (as pyicloud does for 450). If Apple asks for a code, it starts
  re-authentication under Repairs instead of only showing "unavailable" after 30
  minutes. Single 409s are still bridged with the last values.
- AirPods & accessories: no more battery level/state sensors (Apple never reports
  them via iCloud); they are removed on the next reload.
- No more "Play sound" for AirPods & accessories: Apple only plays the sound on
  connected AirPods (never in the closed charging case) and otherwise accepts the
  request silently. The button is removed on the next reload.

## 0.3.1

- New brand icon (cloud with location signal).

## 0.3.0

- "Play sound" button per device using the Find My sound; disabled by default for
  AirPods & accessories.

## 0.2.0

- Messages: a `notify` entity ("Message") per message-capable device for
  `notify.send_message`, plus the action `icloud_next.display_message` with optional
  sound (Find My "Display message").

## 0.1.1

- On load/reload, devices Apple no longer reports are removed together with their
  entities (not while Apple is still loading family devices); entities no longer
  provided (e.g. after turning off family) as well.
- Delete button for devices Apple no longer reports.
- Options: label "Locate interval (minutes)".
- README in English with HACS and setup buttons, German section below.
- Own brand icon (`brand/icon.png`, `brand/icon@2x.png`) for HA 2026.3+.

## 0.1.0

- First release: Find My with active locating, location per person, iCloud storage
  (also per family member), serial number and OS version of account devices.
- 2FA during setup and re-authentication with a choice of push/SMS, no automatic
  sending.
- Family optional (setup and options).
