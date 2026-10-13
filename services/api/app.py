"""MooBee Fetch v0.2.1 -- isolated personal transfer/file manager.

Untrusted tracker/webseed URLs are never forwarded to aria2. Curated tracker
profiles require explicit operator enablement and verified egress isolation.
"""
import asyncio
import time
import os
import re
import secrets
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, quote, urlsplit
from torrents import InvalidTorrent, MAX_TORRENT_BYTES, safe_torrent
from discovery import (DiscoveryError, PROFILES, tracker_options,
                       normalize_hash, torrent_hash)
from media_routes import build_router

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

ROOT = Path(os.environ.get('DOWNLOAD_ROOT', '/downloads'))
RPC_URL = os.environ.get('ARIA2_RPC_URL', 'http://downloader:6800/jsonrpc')
ASSETS = Path(__file__).parent / 'static'
security = HTTPBasic(auto_error=False)
app = FastAPI(title='MooBee Fetch', docs_url=None, redoc_url=None, openapi_url=None)
GID = re.compile(r'^[0-9a-f]{16}$')
BTIH = re.compile(r'^(?:[0-9a-fA-F]{40}|[A-Za-z2-7]{32})$')


@app.middleware('http')
async def no_cache(request, call_next):
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        origin = request.headers.get('origin')
        host = request.headers.get('host', '')
        if (request.headers.get('sec-fetch-site') == 'cross-site' or
                (origin is not None and origin != f'{request.url.scheme}://{host}')):
            return JSONResponse({'detail': 'Cross-origin changes forbidden'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


def authorized(credentials: HTTPBasicCredentials | None = Depends(security)):
    user = os.environ.get('FETCH_USERNAME', '')
    password = os.environ.get('FETCH_PASSWORD', '')
    if (not user or len(password) < 24 or credentials is None or
            not secrets.compare_digest(credentials.username.encode(), user.encode()) or
            not secrets.compare_digest(credentials.password.encode(), password.encode())):
        raise HTTPException(401, 'Authentication required',
                            headers={'WWW-Authenticate': 'Basic realm="MooBee Fetch"'})
    return credentials.username


def magnet(value: str) -> str:
    """Normalize public BTIH magnets, discarding all optional remote URLs.

    Common magnets contain tracker/web-seed URLs (tr/ws/as/xs). Passing
    attacker-supplied destinations into the downloader creates additional
    SSRF/egress risks. This version intentionally uses *only* the info hash
    and a safe display name; DHT peer discovery remains the only source.
    """
    if not isinstance(value, str) or not 20 <= len(value) <= 4096 or value != value.strip():
        raise ValueError('Magnet link must be between 20 and 4096 characters')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Magnet link contains control characters')
    try:
        parts = urlsplit(value)
        if parts.scheme != 'magnet' or parts.path or parts.netloc or parts.fragment:
            raise ValueError('A magnet:? link is required')
        params = parse_qs(parts.query, keep_blank_values=True, max_num_fields=64)
    except ValueError as exc:
        raise ValueError('Invalid or oversized magnet parameters') from exc
    xt = params.get('xt', [])
    if len(xt) != 1 or not xt[0].startswith('urn:btih:') or not BTIH.fullmatch(xt[0][9:]):
        raise ValueError('A single valid BitTorrent v1 info hash is required')
    info_hash = xt[0][9:]
    if len(info_hash) == 40:
        info_hash = info_hash.lower()
    else:
        info_hash = info_hash.upper()
    canonical = f'magnet:?xt=urn:btih:{info_hash}'
    name = params.get('dn', [])
    if len(name) > 1:
        raise ValueError('Multiple display names are not supported')
    if name and name[0]:
        display_name = name[0]
        if (len(display_name) > 200 or
                any(c in display_name for c in ('/', '\\')) or
                any(ord(c) < 32 or ord(c) == 127 for c in display_name)):
            raise ValueError('Invalid magnet display name')
        canonical += '&dn=' + quote(display_name, safe='')
    # All other parameters are dropped, even legitimate HTTP(S) trackers
    # and web seeds. Never forward user-controlled destination URLs to aria2.
    return canonical

class TransferInput(BaseModel):
    uri: str = Field(min_length=20, max_length=4096)
    tracker_profile: Literal['dht', 'ubuntu', 'public'] = 'dht'


def curated_enabled() -> bool:
    return os.environ.get('FETCH_CURATED_TRACKERS_ENABLED', '') == '1'


def discovery_rpc_options(profile: str, infohash: str) -> dict:
    try:
        return tracker_options(profile, infohash, enabled=curated_enabled())
    except DiscoveryError as exc:
        if profile != 'dht' and not curated_enabled():
            raise HTTPException(403, str(exc)) from exc
        raise HTTPException(422, str(exc)) from exc


def magnet_infohash(uri: str) -> str:
    """Read the already normalized xt without ever forwarding input trackers."""
    params = parse_qs(urlsplit(uri).query, max_num_fields=4)
    return normalize_hash(params['xt'][0][9:])


async def rpc(method: str, params: list | None = None):
    secret = os.environ.get('ARIA2_RPC_SECRET', '')
    if len(secret) < 32:
        raise HTTPException(503, 'Downloader unavailable')
    body = {
        'jsonrpc': '2.0', 'id': uuid.uuid4().hex,
        'method': 'aria2.' + method,
        'params': ['token:' + secret, *(params or [])],
    }
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(7.0)) as client:
            resp = await client.post(RPC_URL, json=body)
            if resp.status_code != 200 or len(resp.content) > 2_000_000:
                raise HTTPException(502, 'Downloader request failed')
            result = resp.json()
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(502, 'Downloader unavailable') from exc
    if not isinstance(result, dict) or 'error' in result or 'result' not in result:
        raise HTTPException(502, 'Downloader rejected request')
    return result['result']


FIELDS = ['gid', 'status', 'totalLength', 'completedLength',
          'downloadSpeed', 'uploadSpeed', 'files', 'bittorrent',
          'connections', 'numSeeders', 'errorCode']
# Process-local stall tracker: never logs or stores transfer URLs. It resets on
# web restart; the resulting "stalled" flag is observational, not definitive.
LAST_ACTIVITY = {}
STALL_SECONDS = 10 * 60
MAX_OBSERVED = 512


def public_transfer(record, now=None):
    if not isinstance(record, dict):
        return None
    gid = record.get('gid', '')
    if not GID.fullmatch(str(gid)):
        return None
    names = record.get('files') or []
    name = (record.get('bittorrent') or {}).get('info', {}).get('name')
    if not name and names and isinstance(names[0], dict):
        name = Path(str(names[0].get('path') or '')).name
    name = str(name or 'Fetching metadata')[:200]

    def number(field):
        try:
            return min(max(int(record.get(field, 0)), 0), 10**15)
        except (TypeError, ValueError, OverflowError):
            return 0

    status = str(record.get('status', 'unknown'))[:32]
    size = number('totalLength')
    completed = number('completedLength')
    speed = number('downloadSpeed')
    connections = number('connections')
    seeders = number('numSeeders')
    is_metadata = status == 'active' and size == 0
    observed = time.monotonic() if now is None else now
    # Stop tracking transfers that are no longer active. Memory is bounded.
    if status != 'active':
        LAST_ACTIVITY.pop(gid, None)
        elapsed = 0
    else:
        snapshot = LAST_ACTIVITY.get(gid)
        if snapshot is None or completed > snapshot[1] or speed > 0:
            LAST_ACTIVITY[gid] = (observed, completed)
        elapsed = max(0, observed - LAST_ACTIVITY[gid][0])
        if len(LAST_ACTIVITY) > MAX_OBSERVED:
            LAST_ACTIVITY.pop(next(iter(LAST_ACTIVITY)))
    if is_metadata:
        if connections > 0:
            detail = 'Peer connected; metadata exchange still pending'
        elif elapsed >= STALL_SECONDS:
            detail = 'No peers found for 10+ minutes; check DHT and swarm health'
        else:
            detail = 'Finding peers / fetching metadata'
    elif status == 'active' and speed == 0 and size > completed:
        detail = ('No download progress for 10+ minutes' if elapsed >= STALL_SECONDS
                  else 'Waiting for available pieces')
    elif status == 'active':
        detail = 'Downloading'
    elif status == 'complete':
        detail = 'Completed'
    elif status == 'error':
        detail = 'Transfer failed (see downloader diagnostics)'
    elif status == 'paused':
        detail = 'Paused'
    else:
        detail = status.capitalize()
    return {
        'gid': gid, 'name': name, 'status': status, 'detail': detail,
        'metadata_pending': is_metadata, 'stalled': (
            status == 'active' and elapsed >= STALL_SECONDS and speed == 0
        ), 'connections': connections, 'seeders_reported': seeders,
        'size': size, 'completed': completed, 'speed': speed,
        'upload_speed': number('uploadSpeed'),
        'error_code': str(record.get('errorCode') or '')[:8],
    }


@app.get('/')
def index(_: str = Depends(authorized)):
    return FileResponse(ASSETS / 'index.html', media_type='text/html')


@app.get('/assets/{filename}')
def assets(filename: str, _: str = Depends(authorized)):
    if filename not in ('app.js', 'styles.css'):
        raise HTTPException(404, 'Not found')
    return FileResponse(ASSETS / filename)


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'moobee-fetch'}


@app.get('/api/transfers')
async def transfers(_: str = Depends(authorized)):
    active, waiting, stopped, stats = await asyncio.gather(
        rpc('tellActive', [FIELDS]),
        rpc('tellWaiting', [0, 40, FIELDS]),
        rpc('tellStopped', [0, 40, FIELDS]),
        rpc('getGlobalStat'),
    )
    items = [x for x in (public_transfer(r) for r in active + waiting + stopped) if x]
    return {'items': items[:120],
            'download_speed': int(stats.get('downloadSpeed') or 0),
            'upload_speed': int(stats.get('uploadSpeed') or 0)}


@app.get('/api/diagnostics')
async def diagnostics(_: str = Depends(authorized)):
    """Report local aria2 settings only; no tracker URIs, secrets or peer IPs."""
    opts = await rpc('getGlobalOption')
    if not isinstance(opts, dict):
        raise HTTPException(502, 'Invalid downloader diagnostics')
    return {
        'dht_ipv4': opts.get('enable-dht') == 'true',
        'dht_ipv6': opts.get('enable-dht6') == 'true',
        'dht_listen_port': str(opts.get('dht-listen-port', 'unknown'))[:20],
        'peer_listen_port': str(opts.get('listen-port', 'unknown'))[:20],
        'tracker_mode': ('Curated profiles available; input trackers always stripped' if curated_enabled() else 'DHT only; curated trackers disabled'),
        'network_status': 'Not established by application settings',
        'operator_note': 'Zero connected peers does not prove UDP is blocked.'
    }


@app.get('/api/discovery/profiles')
def discovery_profiles(_: str = Depends(authorized)):
    # Never claim firewall policy exists merely because an operator opted in.
    return {
        'default': 'dht',
        'curated_enabled': curated_enabled(),
        'profiles': list(PROFILES),
        'warning': ('Approved profiles require externally enforced egress'
                    ' isolation; a hostname allowlist does not restrict peers.')
    }


@app.post('/api/transfers/torrent', status_code=202)
async def add_torrent(request: Request, _: str = Depends(authorized)):
    """Raw octet-stream upload (not multipart); never save untrusted metainfo."""
    if request.headers.get('content-type', '').split(';', 1)[0].lower() not in (
        'application/x-bittorrent', 'application/octet-stream'
    ):
        raise HTTPException(415, 'Upload a .torrent file as application/octet-stream')
    payload = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_TORRENT_BYTES - len(payload):
            raise HTTPException(413, 'Torrent metadata exceeds 2 MiB')
        payload.extend(chunk)
    try:
        sanitized = safe_torrent(bytes(payload))
    except InvalidTorrent as exc:
        raise HTTPException(422, str(exc)) from exc
    profile = request.headers.get('x-moobee-discovery-profile', 'dht')
    options = {'dir': '/downloads', 'seed-time': '0'}
    options.update(discovery_rpc_options(profile, torrent_hash(sanitized)))
    result = await rpc('addTorrent', [sanitized, [], options])
    if not isinstance(result, str) or not GID.fullmatch(result):
        raise HTTPException(502, 'Unexpected downloader response')
    return {'gid': result, 'queued': True, 'type': 'torrent'}


@app.post('/api/transfers', status_code=202)
async def add_transfer(payload: TransferInput, _: str = Depends(authorized)):
    try:
        uri = magnet(payload.uri)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    options = {'dir': '/downloads', 'seed-time': '0'}
    options.update(discovery_rpc_options(payload.tracker_profile, magnet_infohash(uri)))
    result = await rpc('addUri', [[uri], options])
    if not isinstance(result, str) or not GID.fullmatch(result):
        raise HTTPException(502, 'Unexpected downloader response')
    return {'gid': result, 'queued': True}


@app.post('/api/transfers/{gid}/{action}')
async def transfer_action(gid: str, action: str, _: str = Depends(authorized)):
    if not GID.fullmatch(gid) or action not in ('pause', 'resume', 'remove'):
        raise HTTPException(404, 'Unknown transfer action')
    method = {'pause': 'pause', 'resume': 'unpause', 'remove': 'forceRemove'}[action]
    await rpc(method, [gid])
    return {'ok': True}


def file_target(relative: str) -> Path:
    if (not isinstance(relative, str) or len(relative) > 512 or
            relative.startswith('/') or '\\' in relative or '\x00' in relative):
        raise HTTPException(400, 'Invalid file path')
    components = Path(relative).parts
    if any(x in ('.', '..') or x.startswith('.') for x in components):
        raise HTTPException(400, 'Invalid file path')
    root = ROOT.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(403, 'Unsafe file path')
    # Refuse symlinks, including symlinks within downloaded directories.
    walk = root
    for piece in components:
        walk = walk / piece
        if walk.is_symlink():
            raise HTTPException(403, 'Symlink file access disabled')
    return path


@app.get('/api/files')
def files(path: str = '', _: str = Depends(authorized)):
    target = file_target(path)
    if not target.is_dir():
        raise HTTPException(404, 'Folder not found')
    result = []
    for file in sorted(target.iterdir(), key=lambda v: (not v.is_dir(), v.name.lower()))[:500]:
        if file.name.startswith('.') or file.is_symlink():
            continue
        if not (file.is_file() or file.is_dir()):
            continue
        relative = file.relative_to(ROOT.resolve()).as_posix()
        result.append({'name': file.name[:250], 'path': relative,
                       'type': 'folder' if file.is_dir() else 'file',
                       'size': file.stat().st_size if file.is_file() else None})
    return {'path': path, 'items': result}


@app.get('/api/files/download')
def download(path: str, _: str = Depends(authorized)):
    target = file_target(path)
    if not target.is_file():
        raise HTTPException(404, 'File not found')
    return FileResponse(target, filename=target.name,
                        media_type='application/octet-stream')

# Stage-only media inspection. Global authentication applies to both routes.
app.include_router(build_router(rpc, ROOT), dependencies=[Depends(authorized)])
