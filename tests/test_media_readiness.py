"""v0.2.3 completed media checks and HTTP safety regression tests."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API = Path(__file__).resolve().parents[1] / "services" / "api"
sys.path.insert(0, str(API))
import media
import media_routes

spec = importlib.util.spec_from_file_location("moobee_media_test_app", API / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

GID = "a" * 16
AUTH = ("tester", "test-password-for-media-readiness-example-123")


def record(path, size, *, status="complete", done=None, selected="true"):
    return {"gid": GID, "status": status, "bittorrent": {"info": {"name": "demo"}},
            "files": [{"index": "1", "path": str(path), "length": str(size),
                       "completedLength": str(size if done is None else done),
                       "selected": selected}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FETCH_USERNAME", AUTH[0])
    monkeypatch.setenv("FETCH_PASSWORD", AUTH[1])
    monkeypatch.setenv("ARIA2_RPC_SECRET", "fake-rpc-secret-for-tests-example-123456")
    monkeypatch.setattr(app, "ROOT", tmp_path)
    c = TestClient(app.app)
    c.auth = AUTH
    return c


def test_eligible_complete_regular_file(client, tmp_path):
    file = tmp_path / "authorized-sample.mp4"
    file.write_bytes(b"demo bytes")
    item = media.eligible(record(file, 10), 1, tmp_path)
    fd = media.open_verified(item, tmp_path)
    try:
        assert os.fstat(fd).st_size == 10
    finally:
        os.close(fd)


@pytest.mark.parametrize("kwargs", [
    {"status": "active"},
    {"status": "paused"},
    {"done": 9},
    {"selected": "false"},
])
def test_partial_and_unselected_files_rejected(tmp_path, kwargs):
    file = tmp_path / "demo.mp4"
    file.write_bytes(b"0123456789")
    with pytest.raises(media.MediaNotReady):
        media.eligible(record(file, 10, **kwargs), 1, tmp_path)


def test_symlink_hardlink_missing_and_size_mismatch(tmp_path):
    target = tmp_path / "real.mp4"
    target.write_bytes(b"some bytes")
    link = tmp_path / "linked.mp4"
    link.symlink_to(target)
    with pytest.raises(OSError):
        media.open_verified(media.eligible(record(link, 10), 1, tmp_path), tmp_path)
    hard = tmp_path / "hard.mp4"
    os.link(target, hard)
    with pytest.raises(media.MediaNotReady):
        media.open_verified(media.eligible(record(hard, 10), 1, tmp_path), tmp_path)
    with pytest.raises(media.MediaNotReady):
        media.open_verified(media.eligible(record(target, 999), 1, tmp_path), tmp_path)
    with pytest.raises(FileNotFoundError):
        media.open_verified(media.eligible(record(tmp_path / "missing.mp4", 10), 1, tmp_path), tmp_path)


def test_outside_hidden_and_unsupported_extension(tmp_path):
    with pytest.raises(media.MediaNotReady):
        media.eligible(record("/etc/passwd", 100), 1, tmp_path)
    with pytest.raises(media.MediaNotReady):
        media.eligible(record(tmp_path / ".hidden.mp4", 100), 1, tmp_path)
    with pytest.raises(media.MediaNotReady):
        media.eligible(record(tmp_path / "script.sh", 100), 1, tmp_path)
    with pytest.raises(media.MediaNotReady):
        media.eligible(record(tmp_path / "nested" / ".." / "escape.mp4", 100), 1, tmp_path)


def test_candidate_listing_and_probe_preview_only(client, tmp_path, monkeypatch):
    file = tmp_path / "test-video.mkv"
    file.write_bytes(b"TEST-FILE" * 5)
    entry = record(file, 45)
    async def fake_rpc(method, params=None):
        if method == "tellActive": return []
        if method == "tellWaiting": return []
        if method == "tellStopped": return [entry]
        if method == "tellStatus": return entry
        raise AssertionError("Unexpected mutation RPC " + method)
    monkeypatch.setattr(app, "rpc", fake_rpc)
    monkeypatch.setattr(media_routes, "inspect_fd", lambda fd: {
        "codec": "h264", "width": 1280, "height": 720, "duration_seconds": 1.0
    })
    r = client.get("/api/media/candidates")
    assert r.status_code == 200
    assert r.json()["import_enabled"] is False
    assert len(r.json()["items"]) == 1
    assert r.json()["items"][0]["status"] == "needs_inspection"
    assert client.get("/api/media/candidates", auth=("tester", "wrong")).status_code == 401
    bad = client.post("/api/media/inspect", json={"gid": GID, "index": 1},
                      auth=("tester", "wrong"))
    assert bad.status_code == 401
    r = client.post("/api/media/inspect", json={"gid": GID, "index": 1})
    assert r.status_code == 200, r.text
    assert r.json()["validated"] is True
    assert r.json()["import_queued"] is False
    assert r.json()["video"]["codec"] == "h264"
    assert "path" not in r.json()


def test_api_never_inspects_paused_or_incomplete_transfer(client, tmp_path, monkeypatch):
    f = tmp_path / "partial.mp4"
    f.write_bytes(b"short")
    entry = record(f, 100, status="paused", done=5)
    async def fake_rpc(method, params=None):
        if method == "tellStatus": return entry
        return [] if method.startswith("tell") else None
    monkeypatch.setattr(app, "rpc", fake_rpc)
    def forbidden(fd):
        raise AssertionError("FFprobe should never run on partial media")
    monkeypatch.setattr(media_routes, "inspect_fd", forbidden)
    r = client.post("/api/media/inspect", json={"gid": GID, "index": 1})
    assert r.status_code == 409
    assert client.get("/api/media/candidates").json()["items"] == []
    assert client.post("/api/media/inspect", json={"gid": "../bad", "index": 1}).status_code == 422
    assert client.post("/api/media/inspect", json={"gid": GID, "index": 0}).status_code == 422


def test_failed_ffprobe_cannot_mark_media_valid(client, tmp_path, monkeypatch):
    file = tmp_path / "bad.mp4"
    file.write_bytes(b"not a video")
    entry = record(file, len(b"not a video"))
    async def fake_rpc(method, params=None):
        return entry
    monkeypatch.setattr(app, "rpc", fake_rpc)
    def rejects(fd):
        raise media.MediaNotReady("Invalid video")
    monkeypatch.setattr(media_routes, "inspect_fd", rejects)
    r = client.post("/api/media/inspect", json={"gid": GID, "index": 1})
    assert r.status_code == 409
    assert "Invalid video" not in r.text
