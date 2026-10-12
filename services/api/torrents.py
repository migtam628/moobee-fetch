"""Strict BitTorrent v1 .torrent ingestion for MooBee Fetch.

Never forward a torrent's announce, announce-list, url-list, nodes, HTTP
seeds, or arbitrary metadata to aria2. Preserve only the original bencoded
info dictionary, which retains its info hash and is suitable for DHT discovery.
Private torrents cannot use this DHT-only import path.
"""
import base64

MAX_TORRENT_BYTES = 2 * 1024 * 1024
MAX_TRANSFER_BYTES = 20 * 1024 ** 3
MAX_FILES = 512
MAX_ENTRIES = 8192
MAX_DEPTH = 24


class InvalidTorrent(ValueError):
    pass


class Decoder:
    def __init__(self, payload):
        self.payload = payload
        self.nodes = 0

    def parse(self, offset=0, depth=0):
        data = self.payload
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_ENTRIES or offset >= len(data):
            raise InvalidTorrent("Invalid torrent metadata")
        marker = data[offset:offset + 1]
        if marker == b"i":
            end = data.find(b"e", offset + 1)
            if end < 0 or end - offset > 22:
                raise InvalidTorrent("Invalid torrent integer")
            value = data[offset + 1:end]
            if (not value or value in (b"-0",) or
                    (value[0:1] == b"0" and len(value) > 1) or
                    value.startswith(b"-0") or
                    any(c not in b"-0123456789" for c in value) or
                    value.count(b"-") > 1 or
                    (b"-" in value and not value.startswith(b"-"))):
                raise InvalidTorrent("Invalid torrent integer")
            try:
                return int(value), end + 1
            except ValueError as exc:
                raise InvalidTorrent("Invalid torrent integer") from exc
        if marker in (b"l", b"d"):
            result = [] if marker == b"l" else {}
            pos = offset + 1
            prev = None
            while pos < len(data) and data[pos:pos + 1] != b"e":
                if marker == b"l":
                    item, pos = self.parse(pos, depth + 1)
                    result.append(item)
                else:
                    key, pos = self.parse(pos, depth + 1)
                    if not isinstance(key, bytes) or (prev is not None and key <= prev):
                        raise InvalidTorrent("Invalid or unsorted torrent keys")
                    prev = key
                    item, pos = self.parse(pos, depth + 1)
                    result[key] = item
            if pos >= len(data):
                raise InvalidTorrent("Unterminated torrent metadata")
            return result, pos + 1
        colon = data.find(b":", offset, min(len(data), offset + 13))
        if colon < 0:
            raise InvalidTorrent("Invalid torrent string")
        digits = data[offset:colon]
        if (not digits or any(c < 48 or c > 57 for c in digits) or
                len(digits) > 1 and digits.startswith(b"0")):
            raise InvalidTorrent("Invalid torrent string length")
        size = int(digits)
        if size > MAX_TORRENT_BYTES or colon + 1 + size > len(data):
            raise InvalidTorrent("Torrent string too large")
        return data[colon + 1:colon + 1 + size], colon + 1 + size


def _component(value):
    if not isinstance(value, bytes):
        raise InvalidTorrent("Invalid torrent path")
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidTorrent("Invalid UTF-8 torrent path") from exc
    if (not 1 <= len(text) <= 200 or text in (".", "..") or text.startswith(".")
            or text.startswith(("~", "/")) or "\\" in text or ":" in text
            or "/" in text or any(ord(c) < 32 or ord(c) == 127 for c in text)):
        raise InvalidTorrent("Unsafe torrent path")
    return text


def safe_torrent(payload: bytes) -> str:
    """Return base64 of sanitized metainfo, never a raw input URL."""
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_TORRENT_BYTES:
        raise InvalidTorrent("Torrent must be 1 byte to 2 MiB")
    decoder = Decoder(payload)
    if payload[:1] != b"d":
        raise InvalidTorrent("Torrent root must be a dictionary")
    # Parse the full document once, then locate exact raw info bytes without
    # re-encoding: a re-encoded dictionary can silently change the info hash.
    meta, pos = decoder.parse()
    if pos != len(payload) or not isinstance(meta, dict):
        raise InvalidTorrent("Invalid torrent metainfo")
    info = meta.get(b"info")
    if not isinstance(info, dict):
        raise InvalidTorrent("Torrent info dictionary missing")
    if info.get(b"private", 0) not in (0,):
        raise InvalidTorrent("Private torrents require tracker access; not supported")
    _component(info.get(b"name"))
    piece_length = info.get(b"piece length")
    if type(piece_length) is not int or not 16384 <= piece_length <= 32 * 1024 * 1024:
        raise InvalidTorrent("Invalid torrent piece length")
    pieces = info.get(b"pieces")
    if not isinstance(pieces, bytes) or len(pieces) % 20:
        raise InvalidTorrent("Invalid torrent piece hashes")
    if b"files" in info:
        files = info[b"files"]
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
            raise InvalidTorrent("Invalid torrent file list")
        total = 0
        seen = set()
        for entry in files:
            if not isinstance(entry, dict):
                raise InvalidTorrent("Invalid torrent file entry")
            length = entry.get(b"length")
            path = entry.get(b"path")
            if type(length) is not int or length < 0 or not isinstance(path, list) or not path:
                raise InvalidTorrent("Invalid torrent file entry")
            parts = tuple(_component(x) for x in path)
            if parts in seen:
                raise InvalidTorrent("Duplicate torrent path")
            seen.add(parts)
            total += length
    else:
        total = info.get(b"length")
        if type(total) is not int:
            raise InvalidTorrent("Invalid torrent size")
    if not 0 < total <= MAX_TRANSFER_BYTES:
        raise InvalidTorrent("Torrent exceeds 20 GiB or has no content")
    if len(pieces) != ((total + piece_length - 1) // piece_length) * 20:
        raise InvalidTorrent("Torrent piece hashes do not match size")
    # Locate original byte span using a bounded parse of root's dictionary.
    pos = 1
    while pos < len(payload) and payload[pos:pos + 1] != b"e":
        key, pos = Decoder(payload).parse(pos)
        start = pos
        _, pos = Decoder(payload).parse(pos)
        if key == b"info":
            return base64.b64encode(b"d4:info" + payload[start:pos] + b"e").decode("ascii")
    raise InvalidTorrent("Torrent info dictionary missing")
