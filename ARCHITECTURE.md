# MooBee Fetch service boundaries

## Separate Compose project

Project: `moobee-fetch`; directory: `/opt/moobee-fetch`; admin port `127.0.0.1:8091`; aria2's JSON-RPC endpoint remains private on Docker project network. Server's existing `/opt/moobee-stream` files, ports 8080/443, Tailnet Serve configuration and media database are never modified.

## First MVP data plane

User -> basic-authenticated web -> validated magnet -> private aria2 RPC -> separate downloads directory. File browser reads the directory read-only, never executing or automatically importing anything.

Magnet transfer type: use valid BitTorrent BTIH magnet URIs. No direct HTTP(S) downloads until egress isolation; any supplied tracker/web-seed URLs are discarded before reaching aria2. Discovery via DHT only. Peer IP exposure and malicious torrent content are risks; avoid deploying unless the operator accepts and hardens this model.

## Later media ingestion

Create a distinct `moobee-fetch-import` worker with read-only access to completed download files. It should validate file type, name, size, quotas and duration; require an explicit operator selection; then POST the approved media file to the private MooBee Stream upload API, using a service-specific, server-held credential. Do not share Stream's administrative API key with browser sessions. MooBee Stream then retains its normal FFmpeg queue and HLS pipeline. MooBee IPTV's explicitly mapped Stream content can be consumed in StreamBoss. Never directly copy files into MooBee Stream's PostgreSQL or media folders.

## Deployment boundaries

Do not publish the downloader JSON-RPC endpoint or qBittorrent-like admin interface to the internet. Do not enable Tailscale Funnel or modify the working Nginx/Tailscale/Caddy config. SSH tunnel to the new localhost-only admin port.
