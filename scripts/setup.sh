#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -e .env ]]; then
  echo 'STOP: .env already exists. Secrets not overwritten.' >&2
  exit 1
fi
if [[ -e data ]]; then
  echo 'STOP: data already exists. Refusing to change it.' >&2
  exit 1
fi
mkdir -p data/downloads
chmod 700 data data/downloads
chown 10001:10001 data/downloads
: > data/downloads/.aria2-session
chown 10001:10001 data/downloads/.aria2-session
chmod 600 data/downloads/.aria2-session
python3 - <<'PY'
import secrets
from pathlib import Path
Path('.env').write_text(
    'FETCH_USERNAME=admin\n'
    f'FETCH_PASSWORD={secrets.token_urlsafe(36)}\n'
    f'ARIA2_RPC_SECRET={secrets.token_urlsafe(36)}\n',
    encoding='utf-8'
)
Path('.env').chmod(0o600)
PY
echo 'Created isolated data/downloads and generated .env (0600).'
echo 'Read the password locally with: grep ^FETCH_PASSWORD= .env'
echo 'Never paste .env or the password into chat.'
