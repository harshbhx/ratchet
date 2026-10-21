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

# The two hosts (FR-11.30): the live gateway at RATCHET_DOMAIN and, with RATCHET_SIM=on (the default), the simulation (mock
# mode, no keys) at RATCHET_SIM_DOMAIN. Each problem below says what to fix, and stops before anything is built or replaced.
domain="$(env_get RATCHET_DOMAIN)"
sim="$(env_get RATCHET_SIM)"; sim="${sim:-on}"
sim_domain="$(env_get RATCHET_SIM_DOMAIN)"
[[ -n "$sim_domain" || -z "$domain" ]] || sim_domain="sim.${domain}"
problems=0
if [[ "$sim" != "on" && "$sim" != "off" ]]; then
  echo "RATCHET_SIM must be on or off, not '$sim': fix $HERE/.env" >&2; problems=1
fi
if [[ "$sim" == "on" && -n "$domain" && "$sim_domain" == "$domain" ]]; then
  echo "RATCHET_SIM_DOMAIN equals RATCHET_DOMAIN: Caddy would refuse two sites with one name; give the simulation its own (default sim.<domain>)" >&2; problems=1
fi
sim_key="$(env_get RATCHET_SIM_BOM_SIGNING_KEY)"
if [[ -n "$sim_key" && "$sim_key" == "$(env_get RATCHET_BOM_SIGNING_KEY)" ]]; then
  echo "RATCHET_SIM_BOM_SIGNING_KEY equals RATCHET_BOM_SIGNING_KEY: the simulation must never sign with the live key" >&2; problems=1
fi
[[ "$problems" == "0" ]] || exit 1
if [[ "$sim" == "off" ]] && ! grep -qE '^RATCHET_SIM_URL=' "$HERE/.env"; then
  echo "warning: RATCHET_SIM=off but .env has no RATCHET_SIM_URL= line: the live pages would link a host that is not served; add an empty RATCHET_SIM_URL=" >&2
fi

echo "== git pull"
git -C "$REPO" pull --ff-only

# Settings the example lists and .env does not set (names only): the gateway's own default applies to each, which may not be
# the example's. Not an error: copy the ones you want from .env.vm.example.
missing="$(bash "$HERE/env_missing.sh" "$HERE/.env.vm.example" "$HERE/.env" | paste -sd ' ' -)"
if [[ -n "${missing// /}" ]]; then
  echo "note: .env does not set these settings from .env.vm.example (the defaults apply): ${missing% }" >&2
fi

echo "== docker compose build"
cd "$HERE"
docker compose pull caddy
docker compose build ratchet ratchet-sim  # one image: the second build is a cache hit

# The gateway's startup check, in the new image with this .env, before the running container is replaced (ADR 0070):
# exit 2 means the new gateway would refuse to start, so stop here and keep the old one serving. Exit 1 (warnings) goes on.
echo "== python -m ratchet.config check (new image, deploy/vm/.env)"
check=0
docker compose run --rm --no-deps -T ratchet python -m ratchet.config check || check=$?
if [[ "$check" -ge 2 ]]; then
  echo "configuration errors (above): fix $HERE/.env and run update.sh again; the running gateway was left as it is" >&2
  exit 1
fi
# The simulation has its own environment: a bad RATCHET_DEMO_* value stops the update here too.
echo "== python -m ratchet.config check (ratchet-sim)"
check=0
docker compose run --rm --no-deps -T ratchet-sim python -m ratchet.config check || check=$?
if [[ "$check" -ge 2 ]]; then
  echo "configuration errors in the simulation's environment (above): fix deploy/vm/docker-compose.yml; the running gateways were left as they are" >&2
  exit 1
fi

echo "== docker compose up -d"
docker compose up -d --remove-orphans

echo "== waiting for the containers to be healthy"
status="healthy"
for svc in ratchet ratchet-sim; do
  cid="$(docker compose ps -q "$svc")"
  s="unknown"
  for _ in $(seq 1 60); do
    s="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
    [[ "$s" == "healthy" ]] && break
    sleep 5
  done
  echo "$svc container health: $s"
  [[ "$s" == "healthy" ]] || status="$s"
done

# Liveness (/healthz, the Docker health check) says the process answers; readiness says it can serve: the ledger can be
# written and a teacher can answer. 200 is ready or degraded (the student endpoint down, the teacher answering), 503 not.
# In mock mode (the simulation) readiness checks only the ledger.
echo "== waiting for GET /readyz (inside each container)"
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
ready=1
for svc in ratchet ratchet-sim; do
  ok=0
  for _ in $(seq 1 24); do
    if docker compose exec -T "$svc" python -c "$readyz"; then ok=1; break; fi
    sleep 5
  done
  echo "$svc readiness: $([[ "$ok" == "1" ]] && echo ready || echo 'not ready (see the checks above)')"
  [[ "$ok" == "1" ]] || ready=0
done

# One host's public paths, each of which must answer 200. With MODE_EXPECTED, /healthz must also report that mode: a
# simulation host answering in live mode is a misconfiguration (it would hold keys), and this catches it.
fail=0
check_host() {
  local base="$1" expect="$2" path code body; shift 2
  for path in "$@"; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${base}${path}" || echo 000)"
    echo "${base}${path} -> ${code}"
    [[ "$code" == "200" ]] || fail=1
  done
  if [[ -n "$expect" ]]; then
    body="$(curl -s --max-time 15 "${base}/healthz" || true)"
    if [[ "$body" != *"\"mode\":\"${expect}\""* && "$body" != *"\"mode\": \"${expect}\""* ]]; then
      echo "the simulation host is not in ${expect} mode (${base}/healthz): its container must be mock, with no keys" >&2
      fail=1
    fi
  fi
}

if [[ -n "$domain" ]]; then
  # Public routes only: /v1/* and /openai/* may be blocked at the edge when RATCHET_API_GUARD=guarded, and the dashboard API needs a
  # key when it is private (RATCHET_PUBLIC_DASHBOARD=0, the gateway's default in live mode; ADR 0061).
  paths=(/ /healthz /readyz /dashboard /scout)
  public="$(env_get RATCHET_PUBLIC_DASHBOARD)"
  if [[ "$public" == "1" || ( -z "$public" && "$(env_get RATCHET_MODE)" != "live" ) ]]; then paths+=(/ratchet/stats); fi
  check_host "https://${domain}" "" "${paths[@]}"
  if [[ "$sim" == "on" ]]; then
    check_host "https://${sim_domain}" mock / /healthz /readyz /dashboard /scout /ratchet/stats
  fi
fi

docker image prune -f >/dev/null
[[ "$status" == "healthy" && "$ready" == "1" && "$fail" == "0" ]] || { echo "update finished with problems: see docker compose logs" >&2; exit 1; }
echo "== ok"
