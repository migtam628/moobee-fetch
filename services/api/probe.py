"""Bounded FFprobe validation for completed, opened media files."""
import json
import subprocess
from media import MediaNotReady

SUPPORTED_CODECS = {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4"}
SUPPORTED_CONTAINERS = {"matroska", "webm", "mov", "mp4", "m4a", "3gp", "3g2", "mj2"}

def inspect_fd(fd: int) -> dict:
    # Fixed argv and pre-opened fd: no user-provided command, URL or path.
    cmd = [
        "/usr/bin/ffprobe", "-v", "error",
        "-protocol_whitelist", "file", "-probesize", "2097152",
        "-analyzeduration", "2000000", "-select_streams", "v:0",
        "-show_entries", "format=format_name,duration:stream=codec_type,codec_name,width,height",
        "-of", "json", f"/proc/self/fd/{fd}"
    ]
    try:
        result = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=10, check=False, pass_fds=(fd,),
            env={"PATH": "/usr/bin:/bin", "LANG": "C"}
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MediaNotReady("Media inspection unavailable") from exc
    if result.returncode != 0 or len(result.stdout) > 16384:
        raise MediaNotReady("Media inspection failed")
    try:
        data = json.loads(result.stdout)
        fmt = data["format"]
        stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
        codecs = str(stream["codec_name"])
        names = set(str(fmt["format_name"]).split(","))
        duration = float(fmt["duration"])
        width = int(stream["width"])
        height = int(stream["height"])
    except (TypeError, ValueError, KeyError, StopIteration, IndexError) as exc:
        raise MediaNotReady("Video metadata not recognized") from exc
    if (not names.intersection(SUPPORTED_CONTAINERS) or
            codecs not in SUPPORTED_CODECS or
            not 0 < duration <= 43200 or
            not 16 <= width <= 8192 or
            not 16 <= height <= 8192):
        raise MediaNotReady("Unsupported media format or duration")
    return {
        "container": str(fmt["format_name"])[:64],
        "codec": codecs, "width": width, "height": height,
        "duration_seconds": round(duration, 2)
    }
