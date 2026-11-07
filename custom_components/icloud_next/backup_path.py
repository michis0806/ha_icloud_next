"""iCloud Drive backup path validation shared by options and transport."""

from __future__ import annotations

import hashlib


def backup_namespace(apple_id: str) -> str:
    """Keep the existing account identity independent of the selected path."""
    return hashlib.sha256(apple_id.strip().lower().encode()).hexdigest()[:24]


def default_backup_path(apple_id: str) -> str:
    """Preserve the original backup folder for accounts without a path option."""
    return f"Home Assistant Backups - {backup_namespace(apple_id)}"


def normalize_backup_path(value: str) -> str:
    """Return a path relative to iCloud Drive; never allow the drive root."""
    if not isinstance(value, str):
        raise TypeError("Invalid backup path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Invalid backup path")
    value = value.strip().strip("/")
    parts = value.split("/")
    if (
        not value
        or len(value) > 1024
        or "\\" in value
        or any(not part or part in {".", ".."} or len(part) > 255 for part in parts)
    ):
        raise ValueError("Invalid backup path")
    return value
