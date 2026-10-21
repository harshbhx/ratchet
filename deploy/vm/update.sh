#!/usr/bin/env bash
# Update the demo on the VM: pull the latest code, rebuild, check the config, restart, wait until ready.
#   sudo -u ratchet /opt/ratchet/deploy/vm/update.sh     (or run it as your admin user; it switches)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

# Run as the user that owns the checkout, so git and Docker never leave root-owned files behind.
owner="$(stat -c %U "$REPO")"
if [[ "$(id -un)" != "$owner" ]]; then
  if [[ "$(id -u)" -eq 0 ]]; then exec runuser -u "$owner" -- "$(readlink -f "$0")" "$@"; fi
  exec sudo -u "$owner" -- "$(readlink -f "$0")" "$@"
fi

env_get() { grep -E "^$1=" "$HERE/.env" 2>/dev/null | tail -n1 | cut -d= -f2- || true; }

[[ -f "$HERE/.env" ]] || { echo "missing $HERE/.env (cp .env.vm.example .env and edit it)" >&2; exit 1; }

echo "== git pull"
git -C "$REPO" pull --ff-only

echo "== docker compose build"
cd "$HERE"
docker compose pull caddy
docker compose build ratchet

# The gateway's startup check, in the new image with this .env, before the running container is replaced (ADR 0070):
# exit 2 means the new gateway would refuse to start, so stop here and keep the old one serving. Exit 1 (warnings) goes on.
echo "== python -m ratchet.config check (new image, deploy/vm/.env)"
check=0
docker compose run --rm --no-deps -T ratchet python -m ratchet.config check || check=$?
if [[ "$check" -ge 2 ]]; then
  echo "configuration errors (above): fix $HERE/.env and run update.sh again; the running gateway was left as it is" >&2
  exit 1
fi

echo "== docker compose up -d"
docker compose up -d --remove-orphans

echo "== waiting for the ratchet container to be healthy"
cid="$(docker compose ps -q ratchet)"
status="unknown"
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
  [[ "$status" == "healthy" ]] && break
  sleep 5
done
echo "container health: $status"

# Liveness (/healthz, the Docker health check) says the process answers; readiness says it can serve: the ledger can be
# written and a teacher can answer. 200 is ready or degraded (the student endpoint down, the teacher answering), 503 not.
echo "== waiting for GET /readyz (inside the container)"
readyz='
import sys, urllib.error, urllib.request
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/readyz", timeout=5)
    code, body = r.status, r.read().decode()
except urllib.error.HTTPError as e:
    code, body = e.code, e.read().decode()
except Exception as e:
    code, body = 0, type(e).__name__
print(code, body)
sys.exit(0 if code == 200 else 1)
'
ready=0
for _ in $(seq 1 24); do
  if docker compose exec -T ratchet python -c "$readyz"; then ready=1; break; fi
  sleep 5
done
echo "readiness: $([[ "$ready" == "1" ]] && echo ready || echo 'not ready (see the checks above)')"

domain="$(env_get RATCHET_DOMAIN)"
fail=0
if [[ -n "$domain" ]]; then
  # Public routes only: /v1/* and /openai/* may be blocked at the edge when RATCHET_API_GUARD=guarded, and the dashboard API needs a
  # key when it is private (RATCHET_PUBLIC_DASHBOARD=0, the gateway's default in live mode; ADR 0061).
  paths=(/ /healthz /readyz /dashboard /scout)
  public="$(env_get RATCHET_PUBLIC_DASHBOARD)"
  if [[ "$public" == "1" || ( -z "$public" && "$(env_get RATCHET_MODE)" != "live" ) ]]; then paths+=(/ratchet/stats); fi
  for path in "${paths[@]}"; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://${domain}${path}" || echo 000)"
    echo "https://${domain}${path} -> ${code}"
    [[ "$code" == "200" ]] || fail=1
  done
fi

docker image prune -f >/dev/null
[[ "$status" == "healthy" && "$ready" == "1" && "$fail" == "0" ]] || { echo "update finished with problems: see docker compose logs" >&2; exit 1; }
echo "== ok"
