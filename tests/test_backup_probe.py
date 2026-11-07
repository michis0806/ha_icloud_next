"""Check the proposed bounded-memory upload adapter without Apple requests."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

spec = importlib.util.spec_from_file_location(
    "probe", Path(__file__).parents[1] / "scripts/probe_icloud_backup.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class GuardedFile:
    """Reject attempts to load the entire payload into memory."""

    def __init__(self, source):
        self.source = source
        self.largest_read = 0

    def read(self, size=-1):
        assert 0 <= size <= 64 * 1024, "Upload tried an unbounded read"
        self.largest_read = max(self.largest_read, size)
        return self.source.read(size)

    def __getattr__(self, name):
        return getattr(self.source, name)


def test_multipart_upload_streams_file(tmp_path):
    """The same file rejected by buffered requests uploads in bounded chunks."""
    path = tmp_path / "backup.tar"
    path.write_bytes(b"backup-content" * 100_000)
    progress = []
    received = []

    def post(url, **kwargs):
        assert url == "https://example.invalid/upload"
        assert "files" not in kwargs
        assert kwargs["headers"]["Content-Type"].startswith("multipart/form-data;")
        body = kwargs["data"]
        while chunk := body.read(64 * 1024):
            received.append(chunk)
        assert sum(map(len, received)) == body.len
        return "uploaded"

    session = SimpleNamespace(post=post)
    with path.open("rb") as source:
        guarded = GuardedFile(source)
        with pytest.raises(AssertionError, match="unbounded"):
            requests.Request(
                "POST", "https://example.invalid/upload", files={"backup.tar": guarded}
            ).prepare()
        source.seek(0)
        with probe.StreamingPosts(session, progress.append):
            result = session.post(
                "https://example.invalid/upload",
                files={"backup.tar": probe.NamedReader(guarded, "backup.tar")},
            )
        assert result == "uploaded"
        assert 0 < guarded.largest_read <= 64 * 1024
    assert session.post is post
    assert path.read_bytes() in b"".join(received)
    assert progress == sorted(progress)
    assert progress[-1] == sum(map(len, received))


def test_upload_adapter_restored_after_error():
    """A failed request does not leave the session's post method replaced."""

    def post(*args, **kwargs):
        raise requests.ConnectionError("offline")

    session = SimpleNamespace(post=post)
    with pytest.raises(requests.ConnectionError), probe.StreamingPosts(session):
        session.post("https://example.invalid/upload")
    assert session.post is post


def test_probe_never_falls_back_to_password_login():
    """Expired tokens cannot silently start login or two-factor notifications."""
    service = object.__new__(probe.SessionOnlyService)
    with pytest.raises(probe.ApprovalRequired):
        service.authenticate()
    with pytest.raises(probe.ApprovalRequired):
        service.request_2fa_code()


def test_probe_requires_interactive_approval_for_protected_drive():
    """The probe does not request PCS device notifications."""
    service = object.__new__(probe.SessionOnlyService)
    service._check_pcs_consent = lambda: {"isICDRSDisabled": True}
    with pytest.raises(probe.ApprovalRequired):
        service._request_pcs_for_service("iclouddrive")
