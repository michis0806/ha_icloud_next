"""Saved-session snapshots must not start login or copy unrelated files."""

import json
from pathlib import Path

import pytest

from custom_components.icloud_next import api


def test_backup_snapshot_only_copies_session_files(tmp_path):
    session = tmp_path / "account.session"
    cookies = tmp_path / "account.cookiejar"
    session.write_text(json.dumps({"session_token": "test-token"}))
    cookies.write_bytes(b"#LWP-Cookies-2.0\n")
    (tmp_path / "unrelated.txt").write_text("do not copy")
    snapshot = api.backup_sitzung_sichern(str(tmp_path))
    assert snapshot == {
        session.name: session.read_bytes(),
        cookies.name: cookies.read_bytes(),
    }


def test_backup_snapshot_rejects_incomplete_json(tmp_path):
    (tmp_path / "account.session").write_text('{"session_token":')
    (tmp_path / "account.cookiejar").write_bytes(b"#LWP-Cookies-2.0\n")
    with pytest.raises(api.ICloudError):
        api.backup_sitzung_sichern(str(tmp_path))


def test_backup_snapshot_retries_concurrent_writes(tmp_path, monkeypatch):
    session = tmp_path / "account.session"
    cookies = tmp_path / "account.cookiejar"
    session.write_text(json.dumps({"session_token": "old-test-token"}))
    cookies.write_bytes(b"#LWP-Cookies-2.0\n")
    original = Path.read_bytes
    changed = False

    def read_bytes(path):
        nonlocal changed
        data = original(path)
        if path == session and not changed:
            changed = True
            path.write_text(json.dumps({"session_token": "new-test-token-longer"}))
        return data

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    snapshot = api.backup_sitzung_sichern(str(tmp_path))
    assert changed
    assert json.loads(snapshot[session.name]) == {
        "session_token": "new-test-token-longer"
    }
