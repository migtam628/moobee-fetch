"""Read-only eligibility checks for MooBee Stream staging.

This module does not upload media, enqueue jobs, or alter downloaded files.
"""
import os
import stat
from pathlib import Path

SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
MAX_BYTES = 20 * 1024**3

class MediaNotReady(ValueError):
    pass

def eligible(record, index, root):
    if record.get("status") != "complete":
        raise MediaNotReady("Transfer is incomplete")
    if not isinstance(record.get("bittorrent"), dict):
        raise MediaNotReady("Only BitTorrent transfers supported")
    files = record.get("files", [])
    if not isinstance(files, list) or len(files) > 512 or type(index) is not int:
        raise MediaNotReady("Invalid file selection")
    item = next((f for f in files if isinstance(f, dict) and f.get("index") == str(index)), None)
    if not item or item.get("selected") != "true":
        raise MediaNotReady("Selected file unavailable")
    try:
        length = int(item["length"])
        downloaded = int(item["completedLength"])
    except (ValueError, KeyError, TypeError) as exc:
        raise MediaNotReady("Invalid transfer details") from exc
    if not 0 < length <= MAX_BYTES or downloaded != length:
        raise MediaNotReady("File incomplete or too large")
    path = item.get("path")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 1024:
        raise MediaNotReady("Invalid file path")
    if "\\" in path or any(ord(c) < 32 for c in path):
        raise MediaNotReady("Invalid file path")
    base = root.resolve()
    try:
        relative = Path(path).relative_to(base)
    except ValueError as exc:
        raise MediaNotReady("File outside downloads") from exc
    parts = relative.parts
    if not parts or len(parts) > 8 or any(p in (".", "..") or p.startswith(".") for p in parts):
        raise MediaNotReady("Unsafe media file path")
    if Path(parts[-1]).suffix.lower() not in SUFFIXES:
        raise MediaNotReady("Unsupported media extension")
    return {"gid": record["gid"], "index": index, "name": parts[-1][:200],
            "size": length, "parts": parts}

def open_verified(item, root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in item["parts"][:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY |
                            os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(item["parts"][-1], os.O_RDONLY | os.O_NOFOLLOW |
                       os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    finally:
        os.close(fd)
    info = os.fstat(leaf)
    if not stat.S_ISREG(info.st_mode) or info.st_size != item["size"] or info.st_nlink != 1:
        os.close(leaf)
        raise MediaNotReady("File missing, modified, or not regular")
    return leaf
