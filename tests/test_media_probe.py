"""FFprobe command and media-type checks without media or network access."""
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))
import probe
from media import MediaNotReady


def metadata(codec="h264", duration="15", width=1280, height=720):
    import json
    return json.dumps({
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": duration},
        "streams": [{"codec_type": "video", "codec_name": codec,
                     "width": width, "height": height}]
    }).encode()


def test_ffprobe_fixed_command_and_redacted_env(monkeypatch):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=metadata())
    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    result = probe.inspect_fd(7)
    assert result["codec"] == "h264"
    assert result["duration_seconds"] == 15
    argv, opts = calls[0]
    assert argv[0] == "/usr/bin/ffprobe"
    assert "-protocol_whitelist" in argv
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"
    assert argv[-1] == "/proc/self/fd/7"
    assert opts["timeout"] == 10
    assert opts["pass_fds"] == (7,)
    assert "FETCH_PASSWORD" not in opts["env"]
    assert opts["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("payload", [
    metadata(codec="unknown"),
    metadata(duration="0"),
    metadata(duration="50000"),
    metadata(width=1),
    metadata(height=99999),
    b"invalid",
    b'{"format":{},"streams":[]}',
])
def test_invalid_probe_rejected(monkeypatch, payload):
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=payload))
    with pytest.raises(MediaNotReady):
        probe.inspect_fd(5)


def test_failed_or_timedout_ffprobe_rejected(monkeypatch):
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=b"{}"))
    with pytest.raises(MediaNotReady):
        probe.inspect_fd(5)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 10)
    monkeypatch.setattr(probe.subprocess, "run", timeout)
    with pytest.raises(MediaNotReady):
        probe.inspect_fd(5)
