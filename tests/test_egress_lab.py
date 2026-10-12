"""Static regression checks for opt-in egress isolation policy boundaries."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_default_compose_keeps_no_netadmin_and_no_public_bind():
    source = (ROOT / "compose.yaml").read_text()
    assert "cap_add:" not in source
    assert "network_mode: host" not in source
    assert "0.0.0.0:8091" not in source
    assert "127.0.0.1:8091:8000" in source
    assert "compose.egress.yaml" not in source


def test_egress_overlay_requires_opt_in_public_ip_and_only_downloader_cap():
    source = (ROOT / "compose.egress.yaml").read_text()
    assert "Dockerfile.egress" in source
    assert "NET_ADMIN" in source
    assert "FETCH_HOST_PUBLIC_IP: " in source
    assert "entrypoint:" in source
    assert "web:" not in source
    assert "network_mode: host" not in source


def test_local_namespace_policy_blocks_private_ipv4_and_ipv6_before_daemon():
    source = (ROOT / "services/downloader/egress-entrypoint.sh").read_text()
    for subnet in (
        "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16",
        "198.18.0.0/15", "224.0.0.0/4",
    ):
        assert subnet in source
    assert 'iptables -w -P OUTPUT DROP' in source
    assert 'ip6tables -w -P OUTPUT DROP' in source
    assert 'FETCH_HOST_PUBLIC_IP' in source
    assert 'exec gosu 10001:10001 "$@"' in source
    assert source.index('iptables -w -P OUTPUT DROP') < source.index(
        'exec gosu 10001:10001 "$@"'
    )


def test_smoke_lab_uses_ephemeral_network_and_synthetic_bytes_only():
    source = (ROOT / "scripts/test-egress-lab.sh").read_text()
    assert 'docker network create "$net"' in source
    assert 'trap cleanup EXIT' in source
    assert 'container:$protected' in source
    assert 'Private Docker network address blocked' in source
    assert 'sha256sum /tmp/fixture.bin' in source
    assert '/opt/moobee-stream' not in source
    assert 'docker compose up' not in source
