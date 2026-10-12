"""MooBee Fetch 0.2.1 curated discovery regression/security tests."""
import base64
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API = Path(__file__).resolve().parents[1] / "services" / "api"
sys.path.insert(0, str(API))
import discovery
from torrents import Decoder, safe_torrent

spec = importlib.util.spec_from_file_location("moobee_fetch_api_021", API / "app.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

UBUNTU = "a3b4136a174e30b121a6bb6ab75b577ea7eda5f3"
TEST_HASH = "0123456789abcdef0123456789abcdef01234567"
MALICIOUS_TRACKER = "http://169.254.169.254/latest/meta-data"


def encode(obj):
    if isinstance(obj, bytes):
        return str(len(obj)).encode() + b":" + obj
    if type(obj) is int:
        return b"i" + str(obj).encode() + b"e"
    if isinstance(obj, dict):
        return b"d" + b"".join(encode(k) + encode(obj[k]) for k in sorted(obj)) + b"e"
    if isinstance(obj, list):
        return b"l" + b"".join(encode(v) for v in obj) + b"e"
    raise TypeError(type(obj))


def sample_torrent():
    info = {b"length": 4096, b"name": b"Safe Ubuntu Test",
            b"piece length": 16384, b"pieces": b"x" * 20}
    return encode({b"announce": MALICIOUS_TRACKER.encode(),
                   b"url-list": MALICIOUS_TRACKER.encode(),
                   b"info": info})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FETCH_USERNAME", "tester")
    monkeypatch.setenv("FETCH_PASSWORD", "long-test-password-for-testing-only-123")
    monkeypatch.setenv("ARIA2_RPC_SECRET", "long-test-rpc-secret-for-testing-only-123")
    monkeypatch.delenv("FETCH_CURATED_TRACKERS_ENABLED", raising=False)
    c = TestClient(mod.app)
    c.auth = ("tester", "long-test-password-for-testing-only-123")
    return c


def test_profile_opt_in_off_by_default_and_does_not_call_aria2(client, monkeypatch):
    calls = []
    async def rpc(method, params=None):
        calls.append((method, params))
        return "a" * 16
    monkeypatch.setattr(mod, "rpc", rpc)
    x = client.get("/api/discovery/profiles")
    assert x.status_code == 200
    assert x.json()["curated_enabled"] is False
    assert MALICIOUS_TRACKER not in x.text
    magnet = f"magnet:?xt=urn:btih:{UBUNTU}&tr={MALICIOUS_TRACKER}"
    y = client.post("/api/transfers", json={"uri": magnet, "tracker_profile": "ubuntu"})
    assert y.status_code == 403
    z = client.post("/api/transfers/torrent",
        headers={"Content-Type": "application/octet-stream",
                 "X-MooBee-Discovery-Profile": "public"},
        content=sample_torrent())
    assert z.status_code == 403
    assert calls == []
    assert client.get("/api/discovery/profiles", auth=("tester", "incorrect")).status_code == 401


def test_explicit_ubuntu_profile_only_for_test_hashes(client, monkeypatch):
    monkeypatch.setenv("FETCH_CURATED_TRACKERS_ENABLED", "1")
    captured = []
    async def rpc(method, params=None):
        captured.append((method, params))
        return "a" * 16
    monkeypatch.setattr(mod, "rpc", rpc)
    attacker_uri = (f"magnet:?xt=urn:btih:{UBUNTU}&dn=Ubuntu"
                    "&tr=http%3A%2F%2F127.0.0.1%2Fadmin"
                    "&ws=http%3A%2F%2F169.254.169.254%2F")
    r = client.post("/api/transfers",
        json={"uri": attacker_uri, "tracker_profile": "ubuntu"})
    assert r.status_code == 202, r.text
    assert captured[0][0] == "addUri"
    assert captured[0][1][1] == {
        "dir": "/downloads", "seed-time": "0",
        "bt-tracker": "http://torrent.ubuntu.com:6969/announce"
    }
    assert "127.0.0.1" not in str(captured)
    assert "169.254.169.254" not in str(captured)
    bad = client.post("/api/transfers",json={
        "uri": f"magnet:?xt=urn:btih:{TEST_HASH}", "tracker_profile": "ubuntu"
    })
    assert bad.status_code == 422
    assert len(captured) == 1


def test_public_profile_never_forwards_uploaded_tracker_urls(client, monkeypatch):
    monkeypatch.setenv("FETCH_CURATED_TRACKERS_ENABLED", "1")
    calls = []
    async def rpc(method, params=None):
        calls.append((method, params))
        return "b" * 16
    monkeypatch.setattr(mod, "rpc", rpc)
    content = sample_torrent()
    r = client.post("/api/transfers/torrent", content=content,
        headers={"Content-Type": "application/octet-stream",
                 "X-MooBee-Discovery-Profile": "public"})
    assert r.status_code == 202, r.text
    method, params = calls[0]
    assert method == "addTorrent"
    assert params[1] == []
    cleaned = base64.b64decode(params[0])
    assert MALICIOUS_TRACKER.encode() not in cleaned
    parsed, pos = Decoder(cleaned).parse()
    assert pos == len(cleaned) and list(parsed.keys()) == [b"info"]
    trackers = params[2]["bt-tracker"]
    assert trackers == ",".join(discovery.CURATED_TRACKERS["public"])
    assert "169.254.169.254" not in str(calls)
    assert client.post("/api/transfers/torrent", content=content,
        headers={"Content-Type": "application/octet-stream",
                 "X-MooBee-Discovery-Profile": "unreviewed"}).status_code == 422
    assert len(calls) == 1


def test_base32_and_invalid_discovery_profiles(client):
    digest = bytes.fromhex(UBUNTU)
    base32 = base64.b32encode(digest).decode()
    assert discovery.normalize_hash(base32) == UBUNTU
    assert discovery.normalize_hash(UBUNTU.upper()) == UBUNTU
    assert discovery.tracker_options("dht", UBUNTU, enabled=False) == {}
    with pytest.raises(discovery.DiscoveryError):
        discovery.tracker_options("ubuntu", TEST_HASH, enabled=True)
    with pytest.raises(discovery.DiscoveryError):
        discovery.tracker_options("public", TEST_HASH, enabled=False)
    with pytest.raises(discovery.DiscoveryError):
        discovery.tracker_options("untrusted", TEST_HASH, enabled=True)
    r = client.post("/api/transfers", json={
        "uri": f"magnet:?xt=urn:btih:{UBUNTU}", "tracker_profile": "other"
    })
    assert r.status_code == 422


def test_peer_connected_metadata_diagnostic():
    mod.LAST_ACTIVITY.clear()
    r = mod.public_transfer({"gid": "f"*16, "status": "active",
      "totalLength": "0", "connections": "1", "numSeeders": "1"}, now=100)
    assert r["metadata_pending"] is True
    assert r["connections"] == 1
    assert "metadata exchange still pending" in r["detail"]
