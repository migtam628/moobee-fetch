"""MooBee Fetch curated tracker selection.

Never trust tr/ws/as/xs from an uploaded magnet or torrent. Curated profiles
are disabled unless the operator explicitly enables them. The operator MUST
enforce downloader-container egress protection before enabling this feature:
tracker allowlisting alone DOES NOT restrict peer IPs or DNS resolution.
"""
import base64
import hashlib
import re

# Deliberately literal, reviewed destinations; no env-sourced or request URLs.
# Availability is not guaranteed and external tracker operators see the VPS IP.
CURATED_TRACKERS = {
    "ubuntu": ("http://torrent.ubuntu.com:6969/announce",),
    "public": (
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.stealth.si:80/announce",
    ),
}
# Ubuntu official test hashes established from user-provided control files.
UBUNTU_TEST_HASHES = frozenset({
    "e4f19cf636409b72571766c2ae89c98fc21a109d",
    "a3b4136a174e30b121a6bb6ab75b577ea7eda5f3",
})
VALID_BT_HASH = re.compile(r"^(?:[a-fA-F0-9]{40}|[A-Za-z2-7]{32})$")
PROFILES = ("dht", "ubuntu", "public")


class DiscoveryError(ValueError):
    pass


def normalize_hash(infohash: str) -> str:
    if not isinstance(infohash, str) or not VALID_BT_HASH.fullmatch(infohash):
        raise DiscoveryError("Invalid v1 BitTorrent info hash")
    if len(infohash) == 40:
        return infohash.lower()
    try:
        return base64.b32decode(infohash.upper()).hex()
    except (ValueError, base64.binascii.Error) as exc:
        raise DiscoveryError("Invalid v1 BitTorrent info hash") from exc


def torrent_hash(sanitized_b64: str) -> str:
    """Digest the exact original info bencoding, never regenerated info."""
    raw = base64.b64decode(sanitized_b64, validate=True)
    if not raw.startswith(b"d4:info") or not raw.endswith(b"e"):
        raise DiscoveryError("Invalid sanitized torrent")
    return hashlib.sha1(raw[7:-1]).hexdigest()


def tracker_options(profile: str, infohash: str, *, enabled: bool) -> dict:
    """RPC options for a trusted preset. Never returns user-provided URLs."""
    if profile not in PROFILES:
        raise DiscoveryError("Unknown discovery profile")
    if profile == "dht":
        return {}
    if not enabled:
        raise DiscoveryError("Curated trackers disabled; operator egress policy required")
    normalized = normalize_hash(infohash)
    if profile == "ubuntu" and normalized not in UBUNTU_TEST_HASHES:
        raise DiscoveryError("Ubuntu tracker profile is limited to official test torrents")
    return {"bt-tracker": ",".join(CURATED_TRACKERS[profile])}
