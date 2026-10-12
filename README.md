# MooBee Fetch v0.2 — Private remote downloader (prototype)

A self-hosted **put.io-inspired** personal download manager. This is an independently designed prototype, not a fork, copy of put.io's interface, or client for its service.

**Status:** v0.1 is deployed separately on the MooBee Stream VPS. **v0.2 is a draft, not deployed.** The web UI and downloader live in a separate Compose project, not connected to MooBee Stream. This is still a prototype, not production ready. Use only material you have a legal right to download and use.

## What works in v0.2

- Personal dashboard, download/upload speeds, transfer progress, pause/resume/remove (removing a transfer does **not** delete downloaded data). v0.2 adds peer count, reported seeder count, metadata-fetching labels and observational stall detection after 10 minutes.
- Add BitTorrent magnet URIs with a valid v1 BTIH info hash. Common optional fields (tracker URLs, web seeds, size hints, etc.) are now discarded rather than rejected: only the validated hash and safe display name reach aria2. Peer discovery relies on DHT, so a magnet backed only by private trackers will not work.
- Download files into a separate mounted folder on this VPS; browse folders and securely download completed files. v0.2 persists IPv4 DHT routing-table state in that folder (not a guarantee of UDP connectivity).
- v0.2: authenticated restricted public \`.torrent\` upload via raw octet-stream (not multipart), 2 MiB metadata / 512 files / 20 GiB declared-size caps, strict paths and **no** announced trackers/web seeds/private torrents. DHT-only for these uploads.
- Authenticated read-only DHT settings and listen-port diagnostics. Does not claim a successful internet UDP test.
- Standalone FastAPI web container + Debian aria2 backend in an independent Compose project.
- Password-protected web UI, read-only file serving, API errors sanitized, private RPC secret, mutation origin checks, no access log on web container.
- No open public listening ports. Local SSH forwarding to `127.0.0.1:8091` for macOS browser access.
- Default speed and memory limits so the existing MooBee Stream transcoder has CPU/memory headroom.

## Not implemented yet

Direct HTTP/HTTPS links (requires SSRF-safe egress and redirect controls), trusted tracker support (blocked until an egress firewall exists), RSS/scheduled rules, file sharing/multi-user accounts, WebDAV/SFTP, search, subtitle management, archive extraction, file deletion, automatic import into MooBee Stream, retention policies and per-user quotas. **Do not mistake this for a fully featured put.io clone.**

## Architecture

```text
Mac browser --SSH tunnel--> 127.0.0.1:8091 on Stream VPS
                                     |
                                     v
                        moobee-fetch-web:8000 (FastAPI)
                            |             |
                private aria2 JSON-RPC    read-only file browser
                            |             |
                     aria2 downloader     |
                            |             |
                   /opt/moobee-fetch/data/downloads

Future explicit integration (not present in v0.1):
  vetted downloaded media
           |
       import worker ----secure service-to-service----> MooBee Stream ingest
                                                      |
                                                 FFmpeg / HLS
                                                      |
                                               MooBee IPTV gateway
                                                      |
                                                   StreamBoss
```

Neither service joins the running MooBee Stream Compose network. Nothing is mounted from MooBee Stream, and neither production VPS has to be restarted or changed.

## Stream VPS install / upgrade (review first)

For the v0.2 upgrade checklist, rollback and data preservation instructions, see [docs/v0.2-rollout.md](docs/v0.2-rollout.md). Do not rerun `scripts/setup.sh` or overwrite the existing v0.1 `.env` / `data/` directory.

**Important:** Unlike put.io's hosted cloud, peers can see the **public IP of your VPS** when it participates in BitTorrent. Check the hosting provider's terms, your bandwidth allowance, laws, and consider an approved network policy. This prototype does not include a VPN or block all egress paths to the VPS's private networks. Start with test downloads that you have permission to distribute, not sensitive/private torrent sources.

Transfer this folder onto the **Stream VPS** at `/opt/moobee-fetch`. Do NOT place it inside `/opt/moobee-stream`, overwrite its `.env`, or merge Compose configurations.

```bash
cd /opt/moobee-fetch
bash scripts/setup.sh
# Docker installed already on Stream VPS
# Audit config without revealing secrets:
docker compose config --quiet
# Builds and runs ONLY the moobee-fetch Compose project
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8091/health
```

Expected health response: `{"status":"ok","service":"moobee-fetch"}`. There should be exactly one new host-port binding, `127.0.0.1:8091 -> web:8000`, and **no** public binding for aria2 RPC or any peer port.

Read the generated web password locally on the Stream VPS (do not paste it into chat or source control):

```bash
grep '^FETCH_USERNAME=' /opt/moobee-fetch/.env
grep '^FETCH_PASSWORD=' /opt/moobee-fetch/.env
```

Use a new **Mac terminal** for your SSH tunnel:

```bash
ssh -o ExitOnForwardFailure=yes -N \
  -L 127.0.0.1:8091:127.0.0.1:8091 root@209.159.148.172
```

Leave that terminal open. Open <http://127.0.0.1:8091> in your **Mac browser** and enter the generated username/password at the browser's Basic Auth prompt. This is local HTTP carried over an encrypted SSH tunnel, **not public HTTP**.

## Operations (Stream VPS ONLY)

```bash
cd /opt/moobee-fetch
# Only this isolated project:
docker compose ps
docker compose logs --tail 40 web
docker compose logs --tail 40 downloader
# Stop THIS prototype, not the existing stream stack:
docker compose down
```

`data/` persists when containers stop. Do not delete data unless intentionally removing fetched files. Do not run `docker compose down -v` on the existing `/opt/moobee-stream` project.

## Limitations and security before wider deployment

1. **No public access**: no `0.0.0.0:8091`, no DNS/Caddy/Tailscale Serve/Funnel changes in v0.1. Use an SSH tunnel or a separate approved, authenticated tailnet-only HTTPS ingress in a later phase.
2. **Downloader network egress**: magnet metadata and trackers can contain destinations outside user-controlled input. Enforce per-container egress policy and review the effects of torrent peer connectivity before production; a bridge network alone is **not** a complete firewall.
3. **Torrent privacy**: swarm peers see your VPS public IP. This is unlike a third-party remote service with its own infrastructure. No built-in VPN or anonymity claims.
4. **Credentials**: HTTP Basic is acceptable here **only** on localhost carried over SSH. Replace with strong session/OIDC/Tailscale identity auth before any broader audience. Never log auth headers, torrent URLs or credentials.
5. **Storage and file hygiene**: Downloads are untrusted input. No automatic execution, archive extraction or media ingest. Before allowing ingestion into MooBee Stream, implement MIME validation, FFprobe limits, size/quota limits, malware and archive screening, and a protected import queue.
6. **Direct URLs**: Disabled in v0.1. A secure fetcher must validate schemes, egress destinations, DNS rebinding, redirects, size and MIME, and disallow internal/tailnet/RFC1918 resources. Do not simply hand arbitrary URLs to aria2.
7. **Quotas/retention**: Currently absent. Use a separate data root and monitor available disk. 4 TB physical storage is shared with the live MooBee Stream stack. A single torrent can fill the VPS unless the operator applies external limits.
8. **Capacity**: Two vCPUs; simultaneous torrent hashing, seeding and transcoding may affect playback. Bound torrent speed and concurrent transfers; benchmark under realistic load.
9. **Docker runtime**: The source and Python tests were checked, but an end-to-end Docker runtime test must be performed on an isolated machine or the Stream VPS only with operator approval.

## API reference (private, Basic Auth)

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Loopback health (unauthenticated) |
| `/api/transfers` | GET | Active/waiting/recent transfers and aggregate speeds |
| `/api/transfers` | POST | Queue a magnet: `{"uri":"magnet:?xt=urn:btih:..."}` |
| `/api/transfers/torrent` | POST | Upload raw public `.torrent` bytes (Content-Type: application/octet-stream; no trackers) |
| `/api/diagnostics` | GET | Safe DHT config, peer-port and network diagnostic limitations |
| `/api/transfers/{gid}/{action}` | POST | `pause`, `resume`, or `remove` |
| `/api/files?path=` | GET | Browse files/folders (no dotfiles/symlinks) |
| `/api/files/download?path=...` | GET | Authenticated read-only file download |

## Development tests

From the unpacked source folder, with Python 3.12 and `pytest`, `httpx` and FastAPI installed:

```bash
pytest -q tests/
```

## Recommended product roadmap

- **v0.3 — Safe remote URL transfer**: isolated egress controls + vetted HTTPS downloads; cancellable queue, quotas, host allowlists and malware/format checks.
- **v0.4 — Media import**: explicit file selection → protected import worker → MooBee Stream admin ingest over a private service network. Never use the admin key in the browser.
- **v0.4 — Library and automation**: thumbnails, folder operations, metadata, history, RSS rules, resumable direct HTTP transfers.
- **v0.5 — Full media hub**: multiuser roles, quotas, secure share links, timed deletion, background media inventory, subtitles and optional read-only WebDAV.
- **v1.0**: validated backups, user isolation, egress sandbox, security reviews, longer soak/load tests, refined dashboard, StreamBoss ingestion workflow.

No Put.io API or paid service is used. The technology is self-hosted and its content/storage policy is entirely under the operator's control.
