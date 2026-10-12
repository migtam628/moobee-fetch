#!/bin/sh
# Opt-in isolation: run as root only to configure this container's own
# network namespace, then execute aria2 as uid 10001. Fail closed on error.
set -eu

if [ "$(id -u)" != "0" ]; then
  echo "Egress firewall requires root only during setup" >&2
  exit 1
fi

# The default image stays unprivileged with no NET_ADMIN. This script is used
# only with compose.egress.yaml. All iptables operations are namespace-local.
iptables -w -P OUTPUT DROP
iptables -w -F OUTPUT

# Normal TCP replies to the authenticated web->aria2 RPC are ESTABLISHED.
iptables -w -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Local embedded Docker DNS at 127.0.0.11, local RPC, loopback.
iptables -w -A OUTPUT -o lo -j ACCEPT

# Deny all reserved/private destinations, INCLUDING RFC1918, CGNAT/Tailscale,
# provider metadata, documentation addresses, and multicast/broadcast.
for cidr in \
  0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 \
  169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 \
  192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 \
  198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 \
  240.0.0.0/4
do
  iptables -w -A OUTPUT -d "$cidr" -j REJECT
done
# Only after the denies are installed can traffic leave to public IPv4.
iptables -w -A OUTPUT -j ACCEPT

# No IPv6 egress. Failure to install IPv6 policy halts downloader startup.
# This is stricter than "--disable-ipv6" on aria2 alone.
ip6tables -w -P OUTPUT DROP
ip6tables -w -F OUTPUT

# Require setup to have succeeded. The daemon itself is never root and
# should not retain NET_ADMIN (Linux normally clears effective caps on setuid).
echo "MooBee Fetch isolated IPv4 outbound policy installed; IPv6 egress blocked."
exec gosu 10001:10001 "$@"
