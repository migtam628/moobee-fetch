#!/usr/bin/env bash
# Isolated, disposable egress lab. Does NOT touch live Compose, .env, sessions,
# existing volumes, other projects, or host firewall rules.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || { echo "Docker required" >&2; exit 1; }
nonce="$(date +%s)-$$"
net="mf-egress-lab-$nonce"
service="mf-lab-server-$nonce"
protected="mf-lab-downloader-$nonce"
image="mf-egress-lab:$nonce"

cleanup() {
  docker rm -f "$protected" "$service" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== BUILD OPT-IN EGRESS IMAGE ==="
docker build --quiet -f services/downloader/Dockerfile.egress \
  -t "$image" services/downloader >/dev/null

echo "=== CREATE DISPOSABLE DOCKER NETWORK ==="
docker network create "$net" >/dev/null
docker run -d --name "$service" --network "$net" \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --entrypoint python python:3.12-slim -m http.server 8110 \
  --bind 0.0.0.0 >/dev/null

# Internal private server, reachable from unprotected container in same lab.
server_ip="$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$service")"
if [[ -z "$server_ip" ]]; then
  echo "Could not determine disposable server IP" >&2
  exit 1
fi
echo "=== POSITIVE CONTROL: UNPROTECTED CONTAINER CAN REACH PRIVATE SERVER ==="
docker run --rm --network "$net" --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  -e LAB_SERVER_IP="$server_ip" --entrypoint python python:3.12-slim -c '
import os, time, urllib.request
url = "http://" + os.environ["LAB_SERVER_IP"] + ":8110/"
for i in range(8):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            assert r.status == 200
            print("Unprotected private control: REACHABLE")
            break
    except Exception:
        if i == 7: raise
        time.sleep(.4)
'

echo "=== START NONROOT DOWNLOADER UNDER NAMESPACE POLICY ==="
docker run -d --name "$protected" --network "$net" \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --cap-add NET_ADMIN \
  --security-opt no-new-privileges:true \
  --entrypoint /usr/local/bin/egress-entrypoint.sh \
  "$image" /bin/sleep 90 >/dev/null

sleep 2
if [[ "$(docker inspect -f '{{.State.Running}}' "$protected")" != "true" ]]; then
  docker logs "$protected" 2>&1 | tail -n 10
  echo "FAIL CLOSED: sandbox policy could not initialize. Do not deploy overlay." >&2
  exit 1
fi
docker logs "$protected" 2>&1 | tail -n 3

echo "=== PROTECTED CONTAINER: PRIVATE IP MUST BE BLOCKED ==="
docker run --rm -i --network "container:$protected" \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  -e LAB_SERVER_IP="$server_ip" \
  --entrypoint python python:3.12-slim - <<'PY'
import os, socket, sys
target = os.environ["LAB_SERVER_IP"]
try:
    socket.create_connection((target, 8110), timeout=3).close()
    print("FAIL: Private Docker network address was reachable")
    sys.exit(1)
except OSError:
    print("PASS: Private Docker network address blocked")

# Local gateway DNS must still work. Docker's embedded resolver is namespace-
# local. No external tracker URLs or transfer data are used.
try:
    answers = socket.getaddrinfo("example.com", 443, socket.AF_INET)
    print("DNS resolution:", "PASS" if answers else "FAILED")
    if not answers:
        sys.exit(1)
except OSError as exc:
    print("DNS resolution failed:", type(exc).__name__)
    sys.exit(1)

try:
    with socket.create_connection(("1.1.1.1", 443), timeout=5):
        print("Public IPv4 TCP 443: REACHABLE")
except OSError as exc:
    print("Public IPv4 TCP 443: UNAVAILABLE (" + type(exc).__name__ + ")")
    # Could be provider/firewall policy; not a reason to approve a VPS rollout.
    sys.exit(2)
PY

echo "=== EGRESS LAB PASS ==="
