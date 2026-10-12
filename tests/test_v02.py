"""v0.2 importer security, peer diagnostics, and stalled metadata regression."""
import base64
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1] / "services" / "api"
sys.path.insert(0, str(API_ROOT))
import torrents

spec = importlib.util.spec_from_file_location("moobee_fetch_api_v02", API_ROOT / "app.py")
appmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appmod)

USER = "tester"
PASSWORD = "long-test-password-example-1234567890"


def benc(value):
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if type(value) is int:
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(benc(x) for x in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(benc(k) + benc(value[k]) for k in sorted(value)) + b"e"
    raise AssertionError(type(value))


def metainfo(name=b"Public Test Content", length=4096, extra=None, *, info_extra=None):
    info = {b"length": length, b"name": name, b"piece length": 16384,
            b"pieces": b"a" * (20 * ((length + 16383) // 16384))}
    if info_extra:
        info.update(info_extra)
    root = {b"announce": b"http://127.0.0.1/private", b"info": info}
    if extra:
        root.update(extra)
    return benc(root)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FETCH_USERNAME", USER)
    monkeypatch.setenv("FETCH_PASSWORD", PASSWORD)
    monkeypatch.setenv("ARIA2_RPC_SECRET", "private-rpc-secret-test-example-123456789")
    monkeypatch.setattr(appmod, "ROOT", tmp_path)
    appmod.LAST_ACTIVITY.clear()
    c = TestClient(appmod.app)
    c.auth = (USER, PASSWORD)
    return c


def test_safe_public_torrent_strips_all_untrusted_network_urls_and_preserves_info():
    raw = metainfo(extra={
        b"announce-list": [[b"http://localhost:8091/steal"]],
        b"url-list": b"http://169.254.169.254/metadata",
        b"httpseeds": [b"https://internal.example/seed"],
        b"nodes": [[b"127.0.0.1", 6881]]
    })
    cleaned = base64.b64decode(torrents.safe_torrent(raw))
    assert cleaned.startswith(b"d4:infod") and cleaned.endswith(b"e")
    for threat in (b"announce", b"url-list", b"httpseeds", b"nodes",
                   b"localhost", b"169.254.169.254", b"127.0.0.1"):
        assert threat not in cleaned
    info, pos = torrents.Decoder(cleaned).parse()
    assert pos == len(cleaned)
    assert sorted(info.keys()) == [b"info"]
    assert info[b"info"][b"name"] == b"Public Test Content"


@pytest.mark.parametrize("data", [
    b"", b"not-a-torrent",
    b"d4:infod4:name3:fooee",  # missing length and pieces
    metainfo(info_extra={b"private": 1}),
    metainfo(info_extra={b"meta version": 2}),
    metainfo(info_extra={b"url-list": b"http://169.254.169.254/"}),
    metainfo(info_extra={b"files": [{b"length": 4096, b"path": [b"ok"], b"attr": b"l"}]}),
    metainfo(name=b"../escape"),
    metainfo(name=b".hidden"),
    metainfo(name=b"bad\\name"),
    metainfo(info_extra={b"length": 21 * 1024**3}),
    metainfo(info_extra={b"pieces": b"short"}),
    metainfo(info_extra={b"piece length": 4}),
    metainfo(info_extra={b"files": [{b"length": 4096, b"path": [b"..", b"evil"]}]}),
    metainfo(info_extra={b"files": [{b"length": 4096, b"path": [b"valid", b".hidden"]}]}),
])
def test_torrent_rejects_untrusted_or_oversized_metadata(data):
    with pytest.raises(torrents.InvalidTorrent):
        torrents.safe_torrent(data)


def test_torrent_upload_is_auth_only_and_sanitized_at_rpc_boundary(client, monkeypatch):
    calls = []

    async def fake_rpc(method, params=None):
        calls.append((method, params))
        return "a" * 16

    monkeypatch.setattr(appmod, "rpc", fake_rpc)
    data = metainfo()
    headers = {"Content-Type": "application/octet-stream"}
    assert client.post("/api/transfers/torrent", content=data, headers=headers,
                       auth=("tester", "wrong")).status_code == 401
    response = client.post("/api/transfers/torrent", content=data, headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["type"] == "torrent"
    assert len(calls) == 1 and calls[0][0] == "addTorrent"
    assert calls[0][1][1] == []
    assert calls[0][1][2] == {"dir": "/downloads", "seed-time": "0"}
    assert b"announce" not in base64.b64decode(calls[0][1][0])
    assert client.post("/api/transfers/torrent", content=b"x"* (torrents.MAX_TORRENT_BYTES+1),
                       headers=headers).status_code == 413
    assert len(calls) == 1


def test_torrent_upload_rejects_cross_origin_and_bad_media_types(client, monkeypatch):
    async def no_rpc(*args, **kwargs):
        raise AssertionError("RPC should not be called")
    monkeypatch.setattr(appmod, "rpc", no_rpc)
    data = metainfo()
    assert client.post("/api/transfers/torrent", content=data, headers={
        "Content-Type": "application/octet-stream", "Origin": "http://evil.example"
    }).status_code == 403
    assert client.post("/api/transfers/torrent", content=data, headers={
        "Content-Type": "text/html"
    }).status_code == 415


def test_peer_metadata_state_and_ten_minute_stall():
    appmod.LAST_ACTIVITY.clear()
    record = {"gid": "a"*16, "status": "active", "totalLength": "0",
              "completedLength": "0", "downloadSpeed": "0",
              "connections": "0", "numSeeders": "0"}
    first = appmod.public_transfer(record, now=100.0)
    assert first["metadata_pending"] is True
    assert first["stalled"] is False
    assert "Finding peers" in first["detail"]
    later = appmod.public_transfer(record, now=701.0)
    assert later["stalled"] is True
    assert "10+ minutes" in later["detail"]
    record["connections"] = "2"
    peer_connected = appmod.public_transfer(record, now=703.0)
    assert "Peer connected; metadata exchange still pending" in peer_connected["detail"]
    record["totalLength"] = "8000"
    record["downloadSpeed"] = "99"
    progressing = appmod.public_transfer(record, now=704.0)
    assert progressing["stalled"] is False
    assert progressing["detail"] == "Downloading"
    assert progressing["connections"] == 2


def test_dht_diagnostics_does_not_leak_sensitive_global_options(client, monkeypatch):
    async def fake_rpc(method, params=None):
        assert method == "getGlobalOption"
        return {"enable-dht": "true", "enable-dht6": "false",
                "dht-listen-port": "6881", "listen-port": "6881",
                "rpc-secret": "superprivate", "bt-tracker": "http://privatehost"}
    monkeypatch.setattr(appmod, "rpc", fake_rpc)
    d = client.get("/api/diagnostics")
    assert d.status_code == 200
    assert d.json()["dht_ipv4"] is True
    assert d.json()["dht_ipv6"] is False
    assert d.json()["dht_listen_port"] == "6881"
    assert "superprivate" not in d.text and "privatehost" not in d.text
    assert client.get("/api/diagnostics", auth=("tester", "wrong")).status_code == 401
