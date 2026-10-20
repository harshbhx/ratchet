#!/usr/bin/env bash
# Update the demo on the VM: pull the latest code, rebuild, restart, check health.
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

echo "== docker compose up -d --build"
cd "$HERE"
docker compose pull caddy
docker compose up -d --build --remove-orphans

echo "== waiting for the ratchet container to be healthy"
cid="$(docker compose ps -q ratchet)"
status="unknown"
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
  [[ "$status" == "healthy" ]] && break
  sleep 5
done
echo "container health: $status"

echo "== GET /healthz (inside the container)"
docker compose exec -T ratchet python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).read().decode())"

domain="$(env_get RATCHET_DOMAIN)"
fail=0
if [[ -n "$domain" ]]; then
  # Public routes only: /v1/* may be blocked at the edge when RATCHET_API_GUARD=guarded.
  for path in / /ratchet/stats /dashboard /scout; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://${domain}${path}" || echo 000)"
    echo "https://${domain}${path} -> ${code}"
    [[ "$code" == "200" ]] || fail=1
  done
fi

docker image prune -f >/dev/null
[[ "$status" == "healthy" && "$fail" == "0" ]] || { echo "update finished with problems: see docker compose logs" >&2; exit 1; }
echo "== ok"
